# Changelog

All notable changes to this project are documented in this file.
This changelog is automatically generated from GitHub Releases.

---

## [0.83.0](https://github.com/datamodel-code-generator/datamodel-code-generator/releases/tag/0.83.0) - 2026-09-24

## Breaking Changes

### Code Generation Changes
* Deduplication now compares referenced models - Models that render identically are only merged when the models they reference also match structurally, so schemas whose same-named references resolve to different definitions in other files no longer collapse into a single class; such models are now emitted separately and may gain suffixed names such as `LeafModel` or `Values1`, changing class names and module contents compared with previous output (#4115)
* Generic schemas using `$dynamicRef` are now specialized per dynamic scope - A plain-name `$dynamicRef` previously resolved lexically within its own schema resource, so every referrer shared one model of the referenced generic. The parser now tracks the dynamic scope of the schemas that referenced a model, binds each `$dynamicAnchor` name to its outermost declaration in that scope, and re-parses a referenced schema as an additional specialized model whenever its bindings differ. Schemas that combine `$dynamicRef` with `$dynamicAnchor`, including remote, embedded, recursive, root-level and tuple `additionalItems` generics, now emit extra model classes with suffixed names and different field types than before, so generated module contents, class names and import sites can change for existing users (#4117)
* Specialized dynamic-scope models shift generated class names - Specialized models are registered under the referencing model and named like the shared model before parsing, so the shared model keeps naming priority and specializations receive derived names such as numeric or `Model` suffixes. Output ordering and names of surrounding models can therefore differ from previous releases, and this applies under `--naming-strategy full-path` and `--collapse-root-models` as well (#4117)
* OpenAPI specifications referencing external JSON Schema generics gain specialized models - `parse_raw` now drains pending dynamic-scope specializations after every specification has been read, so components that reference external schemas rebinding a `$dynamicAnchor` produce additional specialized models instead of reusing a single shared model (#4117)
* Dynamic ref resolution scope for inline resources and merged refs - `$dynamicRef` is now resolved within the schema resource a subschema stands for, namely an inline subschema declaring its own `$id` and the target of a `$ref` merged with sibling keywords, with enclosing and referencing resources still able to override the binding so the outermost `$dynamicAnchor` declaration wins; schemas combining `$dynamicRef` with embedded `$id` resources or with `$ref` plus sibling keywords may now generate different field types and model shapes than before, for example a resource-local anchor type such as `RootModel[str]` where the previous output used the anchor visible in the outer scope (#4119)
* Model reuse no longer merges models whose same-named references point at different models - Deduplication for `--reuse-model`, `--collapse-reuse-models` and `--reuse-scope=tree` previously matched models on rendered output and imports alone, so models that rendered identically but referenced different models sharing a name were incorrectly collapsed into one. Duplicate detection within a module and across modules now also requires the referenced models to match, so these models stay distinct. Affected schemas now generate additional or differently named classes, such as a suffixed `FieldModel` alongside `Field`, extra per-module classes, and inheritance from a shared base instead of a single merged model, which can change class names and module layout that downstream code imports (#4121)
* Merged `$ref` targets now resolve through aliases - A referenced schema that is itself a `$ref` is merged with its own target, so a `$ref` with sibling keywords pointing at an alias now generates the aliased definition instead of the alias node, changing model names and field types in generated output for JSON Schema 2020-12 documents that use `$ref` alongside other keywords (#4122)
* References inside merged targets from other documents are rebased - When a `$ref` with sibling keywords points into another document, nested `$ref` and plain-name `$dynamicRef` values are rewritten to absolute locations before merging, so schemas whose relative references previously resolved against the wrong base now produce different models, imports, and field types (#4122)
* Resolve `$recursiveRef` in merged cross-document `$ref` targets - A self-pointing `$recursiveRef` inside a schema merged in from another document now resolves to the nearest enclosing `$recursiveAnchor` of its defining document, or that document's root, instead of resolving against the merging document; schemas that rely on the previous resolution generate different recursive model references and field annotations, so regenerated models may name different classes than before, and when the merging document itself declares `$recursiveAnchor` the reference is instead left to extend the dynamic scope (#4127)
* OpenAPI component schemas that declare an $id are now registered as schema resources - References naming an $id declared inside a component schema now resolve to that embedded schema when the document itself has no matching target, instead of only being treated as document-relative or external document references, and the OpenAPI parser now parses the normalized specification returned by `_prepare_schema_resources` rather than the original raw specification, so specifications that use $id inside component schemas can produce different models, model names, and field types than before (#4128)
* Merged schema copies now collapse into shared models - Object models parsed from a schema merged in from another document are now recognized as duplicates of an identically named model when their rendered shapes match, even if their dedup keys differ because nested referenced names only converge later in the same pass; schemas that previously produced extra suffixed classes such as a second `Tree`, `Node`, or `Branch` now reuse the single shared model, so generated class names, class counts, and field annotations can change and code importing the previously emitted duplicate class names will need updating (#4133)
* Recursive references inside embedded `$id` resources resolve to the resource root - A `$recursiveRef` located in a subschema that declares its own `$id` but no `$recursiveAnchor` now targets the root of that embedded resource instead of the nearest enclosing `$recursiveAnchor` or the document root, both when the document is parsed directly and when its schemas are merged into another document, so affected fields are annotated with the embedded resource model instead of the outer model, and targets reachable only through `$recursiveRef` are now loaded and can add generated models (#4130)
* Root models of documents referenced only by JSON pointer are named after the document - When an external document is reached only through pointer references into it and its own subschemas refer back to the document root, that root model was previously generated as `Field` or `FieldModel`; it is now named after the file stem, for example `Tree` or `Node`, so class names and field annotations such as `list[Field]` change for such schemas (#4132)

### Error Handling Changes
* New warning for allOf base classes that cannot follow dynamic scope bindings - When an `allOf` base class is referenced from a schema whose dynamic scope would rebind its `$dynamicAnchor`, the parser now emits a `UserWarning` stating that the base class resolves its `$dynamicRef` within its own schema resources. Generation still succeeds, but builds that treat warnings as errors or assert on captured warnings can now fail (#4117)
* New SchemaResourceRefWarning raised for compatibility-resolved references - A reference that keeps resolving against the document, or keeps loading the referenced document, for compatibility while JSON Schema would resolve it to an embedded $id resource now emits the newly added `SchemaResourceRefWarning`, so generation runs that previously completed silently can now surface warnings and can fail where warnings are configured as errors (#4128)

### Default Behavior Changes
* External reference mapping keys are matched against the defining document - Because merged target references are rebased to absolute locations, `--external-ref-mapping` entries must now name the path of the document the reference actually comes from rather than a path relative to the merged target, so existing mappings that relied on the previous matching may silently stop applying (#4122)

<!-- Release notes generated using configuration in .github/release.yml at main -->

## What's Changed
* Rebalance CI test shard weights by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4085
* Run measured slow tests first by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4086
* Balance Windows and macOS test shards by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4087
* Fix release draft validation for stacked pull requests by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4088
* Record per-version CI test shard weights by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4089
* Add Ubuntu coverage test shards by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4090
* Run payload validation only with coverage by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4092
* Bump the github-actions group with 7 updates by @dependabot[bot] in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4096
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4067
* Add generation observation baselines by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4099
* Add parser generation factories by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4100
* Share generation attempt lifecycle by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4101
* Add canonical API declaration references by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4103
* Add explicit OpenAPI API scope by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4104
* Verify API scope generation contracts by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4105
* Add TypeSafe Jev SDK usage example by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4134
* Fix merging models with different referenced types by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4115
* Fix plain-name $dynamicRef in embedded schema resources by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4116
* Fix $dynamicRef generics losing dynamic scope bindings by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4117
* Fix $dynamicRef scope in inline schema resources by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4119
* Fix tree-scope reuse leaving references to removed duplicates by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4120
* Fix model reuse merging models with different referenced types by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4121
* Fix $ref merging with alias and cross-file targets by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4122
* Fix $dynamicAnchor bindings in OpenAPI component schemas by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4123
* Fix pointer $ref from stdin schemas with a root $id by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4124
* Fix $dynamicRef to other documents and JSON pointers by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4126
* Fix $recursiveRef in cross-file merged $ref targets by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4127
* Fix $ref to embedded $id resources in OpenAPI component schemas by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4128
* Fix $recursiveRef targets in embedded $id resources and other documents by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4130
* Fix $anchor references in OpenAPI component schemas by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4131
* Fix Field class name for roots of pointer-referenced documents by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4132
* Fix duplicate models left by merged copies of recursive schemas by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4133


**Full Changelog**: https://github.com/datamodel-code-generator/datamodel-code-generator/compare/0.82.0...0.83.0

---

## [0.82.0](https://github.com/datamodel-code-generator/datamodel-code-generator/releases/tag/0.82.0) - 2026-09-16

## Breaking Changes


### Code Generation Changes
* Constraints are now emitted on recursive container fields - Previously any field that was a self reference had all of its constraints suppressed. The new `_can_apply_constraints` property in the base field model only suppresses constraints for direct self references, so a self referencing field whose type is a container such as `list`, `Sequence`, `dict`, `Mapping`, `set`, `frozenset`, or `tuple` now keeps its size constraints. Pydantic output gains `Field(min_length=..., max_length=...)` and msgspec output gains `Meta(min_length=..., max_length=...)` where the generated code previously had a bare default, for example `friends: list[Pet] | None = None` becomes `friends: list[Pet] | None = Field(None, min_length=1)` (#4071)
* Stricter runtime validation of recursive container payloads - Because the recovered size constraints are now present in the generated models, input data that violates `minItems` or `maxItems` on a recursive array is rejected at validation time instead of being accepted. Existing code that relied on the previously unconstrained generated models may start raising pydantic `ValidationError` or `msgspec.ValidationError` for payloads that used to pass (#4071)

<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

## What's Changed
* Fix builtin formatter wrapping of boolean expressions by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4073
* Fix collapse-root-models dropping array constraints on self-referencing unions by @ysheikh2 in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4071
* Generate validators for not-required groups and presence-only if conditions by @ysheikh2 in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4072
* Fix bare Mapping import dropped when schema validators alias it by @ysheikh2 in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4076
* Allow manual replay of failed release draft analysis by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4079

## New Contributors
* @ysheikh2 made their first contribution in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4071

**Full Changelog**: https://github.com/datamodel-code-generator/datamodel-code-generator/compare/0.81.0...0.82.0

---

## [0.81.0](https://github.com/datamodel-code-generator/datamodel-code-generator/releases/tag/0.81.0) - 2026-09-14

## Breaking Changes


### Code Generation Changes
* Undeclared required keys now retained in variant runtime validation - When combining `--read-only-write-only-model-type` request/response variants with `--schema-validator-type` runtime validators, generated models now keep runtime validation rules for required keys that are not declared as properties (for example keys required via additionalProperties or if/then/else), whereas these rules were previously dropped from the variant models. Regenerated Request and Response models therefore enforce stricter validation and may reject payloads that the previously generated validators accepted (#4055)

<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

## What's Changed
* Fix parser template data reuse by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4054
* Fix required validation in request and response models by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4055
* Share JSON Schema reference rewriting across inputs by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4057
* Move output model decisions out of parsers by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4058
* Move output configuration out of parsers by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4059
* Guard output policy and private parser boundaries by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4060


**Full Changelog**: https://github.com/datamodel-code-generator/datamodel-code-generator/compare/0.80.0...0.81.0

---

## [0.80.0](https://github.com/datamodel-code-generator/datamodel-code-generator/releases/tag/0.80.0) - 2026-09-12

## Breaking Changes


### Code Generation Changes
* Python union annotations preserve unsupported member types - When generating from Python input models, unions containing members such as `Callable` retain each member type and the original union order instead of repeating the first non-null type. Regenerated annotations change for the affected unions and nested containers. (#3947)
* Optional nested factories require a callable empty constructor - With `--use-default-factory-for-optional-nested-models`, an optional child with required constructor fields, including inherited fields, keeps its normal `None` or `UNSET` default instead of receiving a factory that fails when called without arguments. (#3953)
* Safe frozen set items use native Pydantic hash generation - Safely hashable frozen Pydantic v2 models used as set or frozenset items rely on the native hash in place of the explicit identity-hash assignment. Mutable models and models outside the supported safe cases retain the previous generated hash behavior. (#4025)
* GraphQL msgspec typename fields retain separate inherited slots - For affected GraphQL inheritance collisions, generated models keep inherited synthetic typename slots separate from colliding child user fields, which receive distinct Python names while retaining their wire aliases. Python attribute names in these affected hierarchies can differ from earlier output. (#4045)
* Constrained root aliases retain their validation - With `--use-root-model-type-alias`, constraints are retained in the alias where supported; affected roots otherwise use a `RootModel` class instead of an unconstrained alias. Unconstrained aliases retain their prior form, and an existing custom root-alias template keeps its selection and remains responsible for its own constraints. (#3962)
* Eligible compound property names use string key types - Eligible string alternatives in `propertyNames` generate inline constrained string key types instead of nested-model keys. This changes ordinary key annotations for affected schemas; custom schema runtime validators remain conditional on `--schema-validator-type pydantic-v2`. (#3970)
* Opt-in schema validators enforce undeclared required names - With `--schema-validator-type pydantic-v2`, required property names absent from generated fields are checked against the raw object input, including applicable pattern intersections. Payloads missing those required names are rejected where the previous validators accepted them; this fix does not add a validator when the option is disabled. (#3988)
* Mapped allOf roots retain compatible constraints - For affected `allOf` roots with an outer format or type mapping, compatible numeric or string constraints are preserved while constraints incompatible with the mapped runtime type are omitted. Regenerated annotations change for these roots and avoid applying incompatible constraints to date, UUID, or other mapped values. (#4043)
* Eligible property-name alternatives use constrained string keys - For supported `propertyNames` alternatives combining ordinary string constraints with non-string-only branches, generated dictionary keys use the applicable constrained string type instead of retaining the non-string alternatives. References, complex patterns, and unsupported cases retain the existing fallback. (#4046)
* Mapped Avro logical defaults use their physical values - When `--type-mappings` or `--type-overrides` remap Avro logical types to physical types such as `int` or `bytes`, affected defaults use the physical Avro value instead of a logical constructor such as a datetime, decimal, or duration expression. (#4031)
* Combined XSD patterns require Pydantic 2.8 at runtime - Pydantic v2 output for distinct sibling pattern facets or combined inherited and derived facets uses compiled Python regular expressions in place of string patterns. Affected generated models require Pydantic 2.8 or later at runtime; the generator dependency minimum and ordinary single-pattern output are unchanged, and this fix adds no custom runtime validator. (#4048)

### Default Behavior Changes
* Safe frozen set items use value-based hashing - Affected frozen Pydantic v2 set items use native value-based hashing, so equal values can deduplicate where identity-based hashing previously kept them distinct. Mutable models and unsupported field combinations retain the prior behavior; nested models are not required to become frozen. (#4025)

### Error Handling Changes
* XML Schema collision generation is restored - Inputs with unused type-name collisions or collisions between XML field names can generate again instead of triggering the additional blanket collision errors. Existing limitations in representing colliding XML fields remain; this restores earlier handling and does not add complete collision support. (#4034)
* Warning-only Pydantic aliases are accepted - Explicit aliases that merely match a protected prefix and do not conflict with an actual base-model attribute retain the existing warning-only behavior instead of being rejected during generation. Actual attribute conflicts remain subject to the configured conflict checks. (#4038)

<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

## What's Changed
* Fix nightly payload validation by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4028
* Preserve unsupported Python union members by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3947
* Preserve inherited Python field overrides by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3948
* Disambiguate Python model definitions by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3949
* Preserve Python alias field types by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3950
* Fix nested Python type imports by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3979
* Fix specialized Python model reuse by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3980
* Avoid invalid nested default factories by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3953
* Preserve nested Python type metadata by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3981
* Preserve Python RootModel inputs by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3954
* Preserve safe native Pydantic set hashes by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4025
* Fix final RootModel sequence types by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3956
* Preserve XSD generation with unused type collisions by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4034
* Preserve MCP boolean definition references by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4033
* Preserve custom dataclass alias templates by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4032
* Preserve warning-only Pydantic aliases by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4038
* Preserve aliased discriminator generation by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4039
* Limit GraphQL typename collision renaming by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4042
* Preserve Python generic reuse fallback by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4037
* Fix msgspec typename inheritance by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4045
* Preserve generation with unused msgspec enums by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4035
* Fix root model alias constraints by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3962
* Fix numeric allOf intersections by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3963
* Fix compound property name constraints by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3970
* Apply union constraints to matching types by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3971
* Preserve raw pattern property inputs by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3972
* Preserve additional pattern intersections by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3984
* Enforce undeclared required properties by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3988
* Preserve pattern validation with root annotations by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3990
* Respect nested schema resource drafts by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4041
* Preserve compatible allOf format constraints by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4043
* Fix allOf property key constraints by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4044
* Fix non-string property name alternatives by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4046
* Preserve mapped Avro defaults by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4031
* Fix XSD pattern validation in type aliases by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4048
* Fix repeated directory generation by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4049


**Full Changelog**: https://github.com/datamodel-code-generator/datamodel-code-generator/compare/0.79.0...0.80.0

---

## [0.79.0](https://github.com/datamodel-code-generator/datamodel-code-generator/releases/tag/0.79.0) - 2026-09-10

## Breaking Changes

### Error Handling Changes
* Reject unsupported msgspec enum members - When generating `msgspec.Struct` output, enums that would render as a plain `Enum` and contain bool or float members now raise an error such as msgspec.Struct does not support bool Enum members, instead of producing code; schemas that previously generated output will now fail and no file is written (#3928)
* msgspec Struct multiple inheritance now aborts generation on layout conflicts - When generating `msgspec.Struct` models with more than one base class, the generator now validates the inherited slot layouts and raises an `Error` refusing to write any output when the generated bases would produce incompatible instance layouts, whereas previously it emitted a module that only failed later at Python import time; both the CLI (which now exits with an error and writes no file) and the Python API (which now raises `Error`) are affected for such schemas (#3929)
* Aliases now abort generation on conflicts or invalid names - When the `aliases` option is used, an alias value that collides with another field, is not a valid Python identifier, is a Python keyword, or conflicts with a reserved Pydantic or msgspec attribute name now raises an Error and stops generation instead of being silently sanitized or deduplicated (#3936)
* Missing embedded resource anchors now raise an error - A reference to an anchor that does not exist within an embedded schema resource now raises an Error with the message that the embedded schema resource has no such anchor, instead of falling back to a physical document fetch (#3977)

### Default Behavior Changes
* Fail msgspec.Struct generation for bool and float enum aliases - By default, without any new opt-in flag, msgspec.Struct generation now aborts for enums whose bool or float members are not represented as an int or str subclass enum, changing behavior for existing schemas that formerly succeeded (#3928)
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
* Aliases are treated as explicit field names and validated - Values supplied through the `aliases` option are now preserved as the chosen field names and checked against each output backend's naming rules, so configurations that previously produced auto-adjusted output for dataclasses, msgspec, or TypedDict backends may now fail instead of quietly renaming the field (#3936)
* Runtime schema validators now validate Mapping inputs - Generated pydantic v2 runtime validators previously skipped validation and returned the value unchanged whenever the input was not a plain `dict`, so non-dict mapping objects such as `UserDict` or `MappingProxyType` bypassed pattern-property, required-group, conditional-required, property-count, and unique-items checks; they are now treated like dictionaries and validated, which can raise a `ValidationError` for mapping inputs that previously passed through silently (#3975)
* Embedded schema resources now resolve in-document first - References to schemas declared with a nested `$id` are now resolved within the containing document before any file or HTTP lookup, and resource-scoped anchors and JSON pointers are honored, so schemas that previously resolved such references to physical files or remote URLs (including cases where an embedded resource shares a physical filename) can now produce different generated models and different fetch behavior (#3977)

### Code Generation Changes
* Dotted module exports now follow the final package layout - When `treat-dot-as-module` is combined with `all-exports-scope`, export depth and collision prefixes are recomputed against the final package layout and the empty-package re-export `__init__.py` files are emitted after module post-processing, so projects generated with both options together will see different import paths and `__init__.py` contents than before (#3926)
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
* Validator method naming now accounts for more collisions - Generated pydantic v2 validator method names now receive incrementing `_1`, `_2` suffixes when they collide with another validator, an existing field name, or an inherited validator, so models using the validators feature with inheritance or overlapping field names can produce method names that differ from previously generated output (#3942)
* Same-named external validator functions now aliased - Validator functions that share a name across different modules now receive stable import aliases and repeated uses of one function share a single import binding, changing the generated import statements and call expressions compared with prior output (#3942)
<!-- Release notes generated using configuration in .github/release.yml at main -->
* Automatic aliasing of imports shadowed by field names - When a property name matches an imported symbol that is used inside that model's own annotations or defaults, the generator now emits an aliased import such as `Optional as Optional_aliased`, `Field as Field_aliased`, or `list as list_aliased_2` and rewrites the affected annotations and defaults to reference the alias; this runs by default with no opt-in flag, so generated output changes for any schema whose field names collide with imported names (#3943)
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
* Conditional required validation now uses strict JSON equality - Generated pydantic v2 runtime validators emit a new `_json_schema_conditional_equal` helper and stop relying on Python `in` membership, so when an if then else conditional required rule has const or enum values that include booleans or the integers 0 and 1 (or nested objects and arrays containing them) the generated code now distinguishes booleans from numbers and compares objects and arrays deeply, changing both the generated output and the runtime acceptance of payloads for schemas built with `schema-validator-type pydantic-v2` (#3960)
<!-- Release notes generated using configuration in .github/release.yml at main -->
* Generated schema validators now import Mapping - When generating pydantic v2 schema validators with the bundled template, generated modules now include a new `from collections.abc import Mapping as _Mapping` import and every `isinstance(data, dict)` guard becomes `isinstance(data, dict) or isinstance(data, _Mapping)`, while structural `match` cases change from `case dict()` to `case dict() or _Mapping()`; users who compare regenerated output against committed golden files will see these differences (#3975)
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

## What's Changed
* Fix release benchmark PR lookup by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4017
* Resolve referenced Protobuf standard types by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3934
* Reject unsupported msgspec enums by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3928
* Reject incompatible msgspec bases by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3929
* Fix concurrent constraint initialization by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4015
* Fix reused model module imports by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3924
* Fix recursive model reexports by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3926
* Validate explicit field aliases by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3936
* Resolve aliases using final model names by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3937
* Resolve validator name collisions by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3942
* Avoid field type name collisions by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3943
* Fix literal allOf pattern intersections by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3945
* Preserve outer scalar allOf constraints by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3946
* Use JSON equality in conditional validators by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3960
* Preserve inline allOf schema validators by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3974
* Accept mapping inputs in schema validators by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3975
* Preserve input changes during watched generation by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3989
* Resolve embedded schema resources by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3977


**Full Changelog**: https://github.com/datamodel-code-generator/datamodel-code-generator/compare/0.78.0...0.79.0

---

## [0.78.0](https://github.com/datamodel-code-generator/datamodel-code-generator/releases/tag/0.78.0) - 2026-09-09

## Breaking Changes


### Code Generation Changes
* MCP tools schema-instance values preserved verbatim - Converting MCP tools inputs now keeps values under `default`, `const`, `enum`, and `examples` unchanged instead of rewriting internal definition references contained within them, so generated model defaults for MCP tools schemas that embed ref-like values inside these instance keywords can differ from previously generated output (#3891)
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
* JSON Pointer decoding order changed - URI-fragment JSON Pointer tokens are now percent-decoded before the `~0` and `~1` tilde escapes are unescaped, following RFC evaluation order, so a `$ref` fragment that combines percent-encoding with tilde escapes such as `a%7E1b` now resolves to a different definition than before and can change the generated models for those schemas (#3893)
<!-- Release notes generated using configuration in .github/release.yml at main -->
* Exact numeric bounds in allOf intersection - The `_intersect_constraint` logic no longer coerces `minimum`, `maximum`, `exclusiveMinimum`, and `exclusiveMaximum` operands to float before comparing them, so integer bounds beyond the 2^53 exact-float precision limit are now intersected exactly instead of after lossy float rounding, changing the generated `ge`, `le`, `gt`, and `lt` values for schemas that merge such large integer bounds via allOf (#3900)
<!-- Release notes generated using configuration in .github/release.yml at main -->
* Numeric constraint precision preserved for large integers - Constraint keywords `multipleOf`, `exclusiveMinimum`, and `exclusiveMaximum` now retain exact integer values instead of coercing them to floats, so regenerated models for schemas containing integers beyond float precision emit different constraint literals and validate differently than before (#3903)
<!-- Release notes generated using configuration in .github/release.yml at main -->
* allOf merge modes now intersect bounds and enums - When `allof_merge_mode` is any value other than none, overlapping numeric, length, item, and property-count bounds from allOf subschemas are intersected to the tightest value and enum values are reduced to their intersection, instead of the previous deep-merge or concatenation behavior, so generated constraints and enum members differ from prior releases (#3961)
* Retain string length bounds on root-array pattern property adapters - With `field_constraints` and `generate_schema_validators` both enabled and builtin types in use, a root-level array whose items declare a single ASCII-alphanumeric patternProperties key mapping to a length-bounded string now generates a `constr` adapter carrying `min_length`/`max_length` instead of a plain `str`, so regenerated models change output and enforce those bounds, rejecting values that previously passed validation (#3992)
* GraphQL root operation types referenced by unions are now emitted - When a GraphQL schema defines a union that references the query, mutation, or subscription root operation type, the parser now removes that type from the excluded root set and emits it as a concrete model so the union resolves correctly, changing the generated output for such schemas across all output backends (#3910)
* XML Schema occurrence bound combination corrected - When generating models from XSD inputs with nested or combined `maxOccurs`, an unbounded maximum is now propagated instead of computing a finite upper bound, so an unbounded parent combined with a finite child (and similar nesting) no longer emits a `max_length` constraint, changing the generated output for affected schemas (#3939)

### Error Handling Changes
* Empty allOf enum intersections now raise an error - When merging allOf subschemas whose enum values do not overlap, generation now raises a `SchemaParseError` and aborts instead of producing a widened or concatenated enum, so schemas that previously generated successfully can now fail (#3961)
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->
<!-- Release notes generated using configuration in .github/release.yml at main -->

### Default Behavior Changes
* New FutureWarning when Black or isort formatters are explicitly selected - Choosing `black` or `isort` formatters through the CLI, resolved configuration, or the Python API now emits a FutureWarning announcing that Black/isort will become optional in a future release; previously explicit formatter selection produced no warning. Black and isort remain required dependencies and generated output is unchanged, but strict setups that treat FutureWarning as an error may now fail unless the warning is filtered or disable-warnings is used (#4009)
<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

<!-- Release notes generated using configuration in .github/release.yml at main -->

## What's Changed
* Update CHANGELOG for 0.77.0 by @dcg-generated-docs[bot] in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3998
* Preserve MCP schema values by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3891
* Fix JSON Pointer decoding by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3893
* Fix schema reference cycle detection by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3894
* Preserve integer bound precision by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3900
* Preserve numeric constraint precision by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3903
* Fix scalar and array allOf roots by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3905
* Intersect allOf bounds and enums by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3961
* Preserve string bounds in pattern adapters by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3992
* Fix GraphQL root union references by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3910
* Fix GraphQL typename collisions by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3911
* Avoid unused template dependency scans by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3919
* Isolate inflection type checks by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3920
* Fix dataclass aliases on older Pydantic runtimes by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4005
* Clarify formatter selection and default warning by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4006
* Add builtin formatter presets by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4008
* Warn about legacy dependency support by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4002
* Prepare optional Black and isort dependencies by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4009
* Reject conflicting XSD field names by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3933
* Preserve unbounded XSD occurrences by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3939
* Preserve XSD simple content inheritance by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3940
* Fix discriminator field aliases by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3922
* Fix external discriminator mappings by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3923


**Full Changelog**: https://github.com/datamodel-code-generator/datamodel-code-generator/compare/0.77.0...0.78.0

---

## [0.77.0](https://github.com/datamodel-code-generator/datamodel-code-generator/releases/tag/0.77.0) - 2026-09-08

## Breaking Changes


### Code Generation Changes
* Nested Avro bytes and fixed defaults are now decoded to bytes - Avro `bytes` and `fixed` defaults nested inside arrays, maps, and records are now converted to Python bytes literals such as `b'\xff'`, whereas previously only top-level bytes and fixed defaults were converted and nested ones remained as strings, so regenerating affected Avro schemas produces different default values across the pydantic v2, dataclass, and msgspec backends (#3896)
* Dictionary defaults now render as factories for pydantic v2 dataclasses - Fields with a dictionary default now emit `Field(default_factory=...)` and add a `from pydantic import Field` import instead of the previous plain default rendering, so regenerating existing schemas produces different output; `ClassVar` and plain required dictionary fields are additionally kept free of Field statements (#3883)
* Ruff formatter output changed by pinned ruff upgrade - The pinned ruff dependency was bumped from 0.14.10 to 0.16.6, which changes how generated code is formatted when the ruff formatters are used: blank lines are now inserted between import groups, import statements are regrouped and reordered, and combined aliased imports such as a single line importing several names with an `as` alias are split onto separate lines, so files regenerated from the same input will differ from output produced by earlier versions (#3874)
* Avro decimal defaults now emit Decimal values - Avro `bytes` and `fixed` fields carrying a `decimal` logical type with a default now decode the default as a signed big-endian coefficient scaled by the declared scale and emit a `Decimal(...)` expression for backends that support deserialized defaults, replacing the previous raw bytes literal output, so regenerating existing models yields different default values (#3902)
* Avro temporal defaults now emit temporal values - For Avro fields with a temporal logical type such as `date`, `time-millis`, `time-micros`, `timestamp-millis`, `timestamp-micros`, `timestamp-nanos`, or the `local-timestamp` variants, an integer `default` is now rendered as a temporal constructor like `datetime_module.date.fromisoformat` or an ISO-8601 string for backends that map these formats to `str`, instead of the previous raw integer literal, so generated models change for any schema using these defaults; timestamp defaults use UTC while local-timestamp and time defaults are naive (#3901)
* Avro duration defaults now decode to timedelta - Avro `fixed` fields using the `duration` logical type with a default now emit `timedelta(milliseconds=N)` values instead of the raw bytes previously produced, changing generated output for any schema that relies on such defaults (#3907)
* XML Schema nillable now recognizes the `1` lexical value - Elements declared with `nillable="1"` are now treated the same as `nillable="true"` and generate a nullable field, so schemas that previously produced a required field for `nillable="1"` will now produce an optional (nullable) field (#3884)
* Avro unqualified name resolution now prefers the enclosing namespace - When resolving an unqualified Avro reference to a record, enum, or fixed type, the parser previously returned the first matching globally registered name; it now first looks for a matching type in the current enclosing namespace and only falls back to the global name when none exists. For Avro schemas that reuse the same unqualified name in both the null namespace and a nested namespace, generated models now reference the namespaced type instead of the global one, changing the generated output. (#3898)
* Enum list defaults now preserve unmatched values - Previously, when a field default was a list, only values that matched an enum member were kept and any unmatched entries were silently dropped from the generated default; now unmatched entries such as plain strings, None, nested lists, and dictionaries are retained in place while matching entries are converted to enum members, so generated default lists can differ for the same input (#3887)
* Multiple enum sources in a union are converted - The generator now inspects every enum branch of a union data type rather than only a single reference, so a value matching any enum member in the union is converted to that member and unmatched values remain literals, changing generated defaults for union-typed fields (#3887)
* Combined XSD pattern facets change generated regexes - XML Schema restrictions with multiple sibling `xs:pattern` facets previously kept only the last pattern value, and now the parser merges all supported sibling patterns into a single anchored alternation and intersects any inherited base pattern through a nested lookahead, so regenerating from such schemas produces different pattern strings than before (#3908)
* Pydantic outputs gain a python-re engine setting - Pydantic model output for these combined-pattern types now emits a `regex_engine="python-re"` configuration entry that was not present before, because the generated anchored lookahead requires Pydantic's Python regex engine, changing the generated model configuration for affected fields (#3908)
* Type-alias defaults for msgspec Struct now build real Struct instances - When generating msgspec Struct models with type aliases enabled, a field whose default is an object or array and whose declared type is an alias that resolves through chained aliases or unions to a Struct model now emits a default factory that calls `convert` to construct the referenced Struct instance, whereas previously such defaults were emitted as plain literal values because only direct Struct references were converted; empty collections, mapping and dict defaults, primitive aliases, and recursive non-model aliases keep their prior output (#3906)

### Error Handling Changes
* Referenced boolean false definitions now rejected - MCP tool input or output schemas that reference a definition whose value is the boolean `false` now raise an Error reading "Referenced MCP boolean false definition is not supported" instead of emitting a permissive model, so schemas that previously generated code may now fail during conversion (#3890)
* Invalid Avro decimal defaults now raise errors - Decimal defaults whose scale falls outside Python's Decimal range, whose coefficient exceeds the declared precision, or whose fixed encoding length does not match the declared size now raise an error during generation instead of being emitted, so schemas that previously generated successfully may now fail (#3902)
* Unrepresentable Avro temporal defaults now raise errors - Temporal defaults outside the Python date or datetime range, time defaults not within a single day, and nanosecond defaults that cannot be represented exactly at microsecond precision now raise an error and abort generation, whereas these schemas previously generated successfully by emitting the raw integer default (#3901)
* Avro duration defaults with calendar components or wrong size now error - Duration defaults with nonzero months or days, or that are not encoded as exactly 12 bytes with a fixed size of 12, now raise an error during generation instead of decoding to bytes (#3907)

### Default Behavior Changes
* RootModel metadata now reports the field name and alias as `root` - When emitting model metadata for RootModel classes, the emitted field entry now uses `root` for both its name and alias instead of the previously emitted synthetic field name, so consumers of the emitted metadata for root models will see different name and alias values (#3897)
* Dynamic extraction now returns root-model type aliases - The `generate_dynamic_models` runtime helper now includes specialized `RootModel` type-alias classes that are assigned at the top level of the generated code, whereas it previously omitted any class whose defining module was not the generated module, so existing callers receive additional entries in the returned mapping while genuine imported dependencies remain excluded (#3904)

<!-- Release notes generated using configuration in .github/release.yml at main -->

## What's Changed
* Ignore black and isort updates in Dependabot by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3855
* Refresh PyPI metadata in Docker builds by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3857
* Improve release benchmark charts by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3860
* Fix msgspec payload test options by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3863
* Fix generation cache and loader regressions by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3865
* Guard shared model boundaries by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3866
* Test dynamic model cache ordering by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3871
* Update Ruff to 0.16.6 by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3874
* Exclude maintenance from release notes by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3877
* Clarify OpenAPI parameter scopes by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3879
* Fix relative YAML cache paths by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3880
* Fix Avro container defaults by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3896
* Fix MCP definition references by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3890
* Fix float configuration output by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3882
* Fix Avro namespace resolution by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3898
* Fix Avro temporal defaults by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3901
* Fix Avro decimal defaults by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3902
* Fix Avro duration defaults by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3907
* Preserve unmatched enum list defaults by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3887
* Fix dataclass Field imports by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3883
* Fix XSD nillable booleans by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3884
* Fix relative template directories by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3885
* Fix nullable schema warnings by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3886
* Preserve disabled CLI options by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3888
* Fix path list inputs by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3895
* Fix root model metadata by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3897
* Fix dynamic RootModel aliases by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3904
* Fix msgspec alias defaults by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3906
* Fix XSD pattern alternatives by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3908
* Fix msgspec GraphQL list defaults by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3909
* Fix Protobuf input detection by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3913
* Preserve Protobuf option literals by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3914
* Reduce test CI overhead by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3968
* Fix repository references after organization transfer by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3995


**Full Changelog**: https://github.com/datamodel-code-generator/datamodel-code-generator/compare/0.76.2...0.77.0

---

## [0.76.2](https://github.com/datamodel-code-generator/datamodel-code-generator/releases/tag/0.76.2) - 2026-09-04

## Breaking Changes

These corrections change generated output or existing configuration behavior for the affected cases. Regenerate and review affected models before upgrading snapshots or downstream integrations.

### Code Generation Changes

* **Avro field defaults are now honored.** Fields with schema defaults are no longer always treated as required; generated fields receive those defaults. Top-level `bytes` and `fixed` defaults become Python bytes using Avro's code-point mapping, including named fixed types. Review constructor requirements and default values. This release does not yet recursively decode nested bytes defaults. (#3842)
* **GraphQL operation roots are identified from the schema.** Actual query, mutation, and subscription roots are omitted even when they have custom names; ordinary types named `Query` or `Mutation` are no longer excluded solely by name. References to omitted operation roots become `Any` instead of unresolved class names. Review imports of previously emitted root classes and affected annotations. (#3842)
* **Backend-specific fields render differently.** Pydantic v2 dataclasses use factories for mutable defaults. Msgspec null fields retain `None` alongside `UnsetType`, and boolean enum values use supported non-literal types rather than boolean `Literal` annotations. TypedDict extra-item types now bring their required imports. Review generated annotations, default factories, and snapshots. (#3840)
* **Root collapsing and model reuse preserve more schema information.** Root wrappers are retained where collapsing would lose container constraints or mishandle cyclic dictionary-key references. Field renaming retains existing source aliases, msgspec inherited fields retain unique wire aliases, and dataclass/msgspec constructor fields are reordered when necessary. Generated module contents and imports also change where reuse previously overwrote package initializers or lost module ownership. (#3841)
* **OpenAPI parameter overrides and discriminator mappings are corrected.** Operation-level parameters override matching path-level parameters by name and location, including referenced parameters, rather than producing duplicates. Short discriminator mappings are recognized, and OpenAPI/AsyncAPI discriminator state is cleared between documents. Review parameter fields and discriminated-union output. (#3843)
* **External JSON Schema anchors take precedence over legacy shorthand pointers.** A reference such as `other.json#name` resolves a matching anchor within that schema resource before falling back to the legacy `/name` pointer. Schemas that relied on the old pointer interpretation when an anchor also exists can resolve to a different definition. (#3838)

### Configuration and Error Handling Changes

* **Relative paths in `pyproject.toml` resolve from the configuration file's directory.** This applies to input, output, custom headers/templates, comparison output, model metadata, local HTTP references, and lockfiles, including batch jobs. When invoking from another directory, adjust relative values or use absolute paths if you previously relied on the working directory. Explicit CLI paths retain their command-line interpretation. (#3845)
* **Msgspec CLI defaults are applied after configuration merging.** Msgspec enables annotations when that setting is implicit, including when selected through configuration; an explicit `use-annotated = false` is respected. Implicit field constraints follow the final annotation setting. Review output if CLI, presets, and `pyproject.toml` previously supplied conflicting options. (#3845)
* **Invalid inputs produce more specific failures.** Avro bytes/fixed defaults containing code points above 255 are rejected. Invalid special-field-name prefixes and references with multiple fragment delimiters are rejected explicitly. Directory references, directory file-output targets, unreadable custom headers, and custom-formatter import failures now produce generator `Error` diagnostics rather than leaking the previous lower-level exceptions. CLI configuration validation and input-decoding failures report concise errors. Update exception handlers or exact-error assertions where applicable. (#3838, #3842, #3845)

## Compatibility Notes

* Explicit default HTTP/HTTPS ports are normalized in remote-lock URL handling; review persisted lockfile comparisons if they distinguish `:80` or `:443` from an omitted port. (#3844)
* Generation temporarily raises the process-wide young-generation garbage-collection threshold and restores it when the outermost generation finishes. Existing disabled-GC state is respected. Hosts inspecting GC thresholds during generation will observe this temporary change. (#3848)

## What's Changed

* Harden JSON Schema resolution by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3838
* Preserve reference naming invariants by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3839
* Correct model backend output by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3840
* Preserve parser model reuse by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3841
* Correct input format semantics by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3842
* Correct OpenAPI and AsyncAPI generation by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3843
* Harden runtime edge cases by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3844
* Harden CLI configuration and errors by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3845
* Tune GC thresholds during generation by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3848
* Speed up PyYAML document construction by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3849


**Full Changelog**: https://github.com/datamodel-code-generator/datamodel-code-generator/compare/0.76.1...0.76.2

---

## [0.76.1](https://github.com/datamodel-code-generator/datamodel-code-generator/releases/tag/0.76.1) - 2026-09-02

## Highlights

* Parser source loading, run state, output-model capabilities, field-name policies, and input-model transport have been separated to clarify internal ownership. Compatibility imports and the public field-name resolver mapping are retained. (#3818, #3820, #3821, #3822, #3823, #3824, #3825)
* Root-model collapsing tracks references incrementally, and the built-in formatter skips unnecessary string normalization. These optimizations retain the existing generated-output behavior. (#3826, #3827, #3828)
* `x-enum-descriptions` now accepts null entries, allowing schemas with missing individual enum descriptions to generate successfully. (#3835)

## What's Changed

* Optimize collapsed root model replacement by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3826
* Track collapsed root model references incrementally by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3827
* Skip unnecessary built-in string normalization by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3828
* Fix runtime validator output model ownership by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3817
* Move parser source loading by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3818
* Move field name policies to output models by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3820
* Decouple parser run lifecycle by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3821
* Move parser recursion signal by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3822
* Move output template context to models by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3823
* Isolate parser source context by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3824
* Decouple input model transport by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3825
* Enforce architecture boundaries by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3832
* Allow null enum descriptions by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3835


**Full Changelog**: https://github.com/datamodel-code-generator/datamodel-code-generator/compare/0.76.0...0.76.1

---

## [0.76.0](https://github.com/datamodel-code-generator/datamodel-code-generator/releases/tag/0.76.0) - 2026-08-29

## Breaking Changes

### Code Generation Changes

* **Pydantic v2 extra-property keys now enforce `propertyNames`.** When `additionalProperties` specifies a value schema, its generated dictionary key type now includes supported string-key constraints from `propertyNames`, instead of always using plain `str`. Regenerated annotations and imports change, and keys that violate those constraints can fail validation. Review affected extra-property inputs and generated-code snapshots. (#3794)

### API/CLI Changes

* **`--list-deprecations` output changed.** Table and Markdown output add `Status` and rename the `Warning since` heading to `Since`. Table lines no longer retain trailing padding. JSON adds `status` while retaining the existing `warning_since` key. Update scripts that depend on exact columns, whitespace, or JSON field sets. (#3810)

```text
# Before (table)
ID   Kind   Target   Warning since   Removal   Replacement

# After (table)
ID   Status   Kind   Target   Since   Removal   Replacement
```

## Compatibility Notes

* Decimal-typed fields whose defaults remain serialized now emit `DefaultValueTypeWarning`; the existing generated default stays unchanged when Decimal deserialization is disabled. Use `--deserialize-default-values decimal` to generate compatible `Decimal` defaults, or account for this warning if your application promotes warnings to errors. The new dated `standard-*-20260826` and `practical-*-20260826` presets enable Decimal and enum default deserialization; existing `*-20260619` presets remain available. (#3792, #3811)
* `--set-default-enum-member` is registered as a scheduled deprecation. It still works and does not emit a runtime deprecation warning in this release. Prefer `--deserialize-default-values enum`; no removal version is set. (#3810, #3811)

## What's Changed

* Cache local reference file resolution by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3799
* Reuse empty Pydantic field render plans by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3800
* Optimize unconstrained array schemas by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3801
* Skip child traversal for leaf schemas by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3802
* Install bundled Agent Skill by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3804
* Validate Pydantic extra property names by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3794
* Deserialize Decimal defaults by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3792
* Structure runtime expression imports by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3795
* Complete scheduled deprecation support by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3810
* Deserialize enum defaults by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3811
* Move shared parser helpers by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3805
* Refactor input model compatibility by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3806
* Decouple parser backend capabilities by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3807
* Enforce architecture boundaries by @koxudaxi in https://github.com/datamodel-code-generator/datamodel-code-generator/pull/3808


**Full Changelog**: https://github.com/datamodel-code-generator/datamodel-code-generator/compare/0.75.1...0.76.0

---

## [0.75.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.75.1) - 2026-08-24

## What's Changed
* Update CHANGELOG for 0.75.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3787
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3788
* fix(parser): preserve recursive root models by @gtxy27 in https://github.com/koxudaxi/datamodel-code-generator/pull/3786
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3775

## New Contributors
* @gtxy27 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3786

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.75.0...0.75.1

---

## [0.75.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.75.0) - 2026-08-24

## Breaking Changes


* minProperties/maxProperties now generate runtime validators - When using the experimental `--generate-schema-validators` option, `minProperties`/`maxProperties` constraints on named object models are now emitted as Pydantic v2 model validators. Previously these constraints were ignored. Data that omits or exceeds the allowed property count will now be rejected at validation time, and generated models gain a new `__json_schema_property_count_rule__` class variable (#3780)
* Runtime-validation helper base class renamed in mixed modules - When a module contains both core validators (patternProperties / required groups / conditional required) and the new property-count validators, the shared helper base class previously named `_JsonSchemaRuntimeValidationBase` is now split: the core helper is renamed to `_JsonSchemaRuntimeValidationBaseCore` and a new `_JsonSchemaRuntimeValidationBase` subclass is inserted. Code that references the generated helper class name by hand will need to be updated (#3780)

```python
# Before (--generate-schema-validators)
class _JsonSchemaRuntimeValidationBase(BaseModel):
    __json_schema_conditional_required__: ClassVar[tuple[Any, ...]] = ()
    ...


class ApiConditionalEnvelopeRequestModel(_JsonSchemaRuntimeValidationBase):
    __json_schema_conditional_required__: ClassVar[tuple[Any, ...]] = ...


# After
class _JsonSchemaRuntimeValidationBaseCore(BaseModel):
    __json_schema_conditional_required__: ClassVar[tuple[Any, ...]] = ()
    ...


class _JsonSchemaRuntimeValidationBase(_JsonSchemaRuntimeValidationBaseCore):
    __json_schema_property_count_rule__: ClassVar[tuple[Any, ...]] = ()
    ...


class ApiConditionalEnvelopeRequestModel(_JsonSchemaRuntimeValidationBase):
    __json_schema_property_count_rule__: ClassVar[tuple[Any, ...]] = (2, 2)
    __json_schema_conditional_required__: ClassVar[tuple[Any, ...]] = ...
```

## What's Changed
* Update CHANGELOG for 0.74.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3773
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3774
* Bump the github-actions group with 6 updates by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3778
* Bump python from 3.14.6-slim-bookworm to 3.14.7-slim-bookworm by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3779
* fix(jsonschema): generate property count validators by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3780
* fix(jsonschema): generate uniqueItems validators by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3781
* Fix payload runtime validation exclusions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3784


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.74.0...0.75.0

---

## [0.74.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.74.0) - 2026-08-17

## Breaking Changes

### Code Generation Changes
* Non-finite float values now render as structural `float(...)` expressions - Generated code for non-finite floats (`inf`, `-inf`, `nan`) from Protocol Buffers, XML Schema, and AsyncAPI-embedded schemas — as field defaults, constraint bounds, list items, and enum values — now renders inline as `float('inf')`, `float('-inf')`, and `float('nan')` instead of emitting bare `inf`/`nan` literals with an injected `from math import inf, nan` header. Users relying on the previous output (e.g. snapshot/golden tests, or importing `inf`/`nan` from the generated module) will see different generated code (#3770)

```python
# Before
from math import inf


class Model(BaseModel):
    value: float | None = inf


# After
class Model(BaseModel):
    value: float | None = float("inf")
```

## What's Changed
* Update CHANGELOG for 0.73.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3767
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3768
* Deploy release benchmark data updates by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3771
* fix(parser): render non-finite values structurally by @Aryan-Pardeshi in https://github.com/koxudaxi/datamodel-code-generator/pull/3770

## New Contributors
* @Aryan-Pardeshi made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3770

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.73.0...0.74.0

---

## [0.73.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.73.0) - 2026-08-15

## Breaking Changes


### Error Handling Changes
* Additional imports are now validated as Python import paths - Values passed via `--additional-imports`, the Python config API (`GenerateConfig`, `JSONSchemaParserConfig`, etc.), or `--extra-template-data` must now be dotted sequences of Python identifiers. Previously any value was accepted and split on commas without validation; now inputs that are not valid import paths (e.g. containing newlines, semicolons, or non-identifier syntax) raise an `Error` and abort generation instead of being emitted into the generated output. Valid dotted paths (optionally whitespace-padded) continue to work unchanged. (#3763)
```
additional_imports must be a Python import path composed of identifiers: 'collections.deque\nINJECTION_MARKER = 1'
```

### Default Behavior Changes
* Reserved template keys in `--extra-template-data` now raise an error for built-in templates - When rendering a built-in (project-owned) template, supplying any generator-reserved key through `--extra-template-data` (or the `extra_template_data` API argument) now raises an `Error` and aborts generation instead of injecting the value. The reserved keys are `class_body_lines`, `config_items`, `schema_runtime_validation`, `schema_runtime_validation_base_class_name`, `schema_runtime_validation_use_base`, `sequence_base_class`, `sequence_item_type`, `sequence_slice_type`, `_safe_config_items`, `typed_dict_kwargs`, and `typed_dict_kwargs_suffix`. To inject raw code via these keys you must now use a custom root template through `--custom-template-dir`. (#3765)
* Stricter `extra_template_data` validation - `extra_template_data` that is not a dictionary, contains non-string keys, or contains duplicate (normalized) keys now raises an `Error` rather than being silently accepted. (#3765)

### Code Generation Changes
* Built-in templates now serialize `extra_template_data` values as non-executing literals - For built-in templates, user-supplied values that were previously emitted as raw Python source are now serialized as quoted, non-executing literals. This affects GraphQL scalar `py_type`, TypedDict `additionalPropertiesType`, `ConfigDict` values, msgspec `base_class_kwargs`, and comments. Only bare or dotted identifiers (e.g. `datetime.date`) are still emitted unquoted; more complex expressions become string literals. For example, a scalar `py_type` supplied as a type expression is now rendered as:
```python
Evil = TypeAliasType("Evil", "__import__('os').system('id') or str")
```
Trusted custom root templates (`--custom-template-dir` providing the root template) keep the previous unrestricted raw behavior. (#3765)
* Include-only custom template directories no longer receive raw built-in context - "Custom root" detection changed from `template_file_path.is_absolute()` to `_uses_custom_root_template`. A `--custom-template-dir` that only supplies include/partial templates (not the model's root template) no longer opts the built-in root into the unrestricted raw-context path; its `extra_template_data` is now treated with the hardened built-in rules (and reserved keys raise an error). (#3765)

## What's Changed
* Update CHANGELOG for 0.72.4 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3736
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3737
* Guard refactor contracts by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3739
* Tighten generation types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3740
* Simplify generation dispatch by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3741
* Simplify parser metadata flow by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3742
* Optimize built-in generation performance by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3738
* Optimize simple field construction by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3754
* Optimize msgspec unset field rendering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3755
* Optimize false reference handling by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3750
* Compile built-in templates by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3757
* Optimize Pydantic field name resolution by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3758
* Avoid Jinja in the playground runtime by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3759
* Fix main lint workflow by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3761
* Bump the github-actions group with 5 updates by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3751
* Fix payload runtime validation exclusions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3764
* Validate additional import paths by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3763
* Harden built-in template data by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3765
* Restrict template source paths by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3766


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.72.4...0.73.0

---

## [0.72.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.72.4) - 2026-08-12

## What's Changed
* Update CHANGELOG for 0.72.3 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3724
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3725
* Update package metadata validation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3733
* Fix nullable forward reference unions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3732
* Split generation orchestration into phases by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3726
* Split result emission paths by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3727
* Split parser processing phases by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3728
* Group single-module processing steps by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3729
* Remove Astral sponsor logo by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3735


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.72.3...0.72.4

---

## [0.72.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.72.3) - 2026-08-10

## What's Changed
* Update CHANGELOG for 0.72.2 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3715
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3721
* Watch local generation dependencies by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3717
* Add batch generation jobs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3716
* Compare generated inputs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3718
* Lock remote schema references by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3719
* Bump pymdown-extensions from 11.0 to 11.0.1 by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3722
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3723


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.72.2...0.72.3

---

## [0.72.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.72.2) - 2026-08-06

## What's Changed
* Bump aiohttp from 3.13.3 to 3.14.3 by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3701
* Update CHANGELOG for 0.72.1 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3702
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3703
* Bump cryptography from 46.0.5 to 50.0.0 by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3700
* Speed up parser hot paths by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3704
* Suggest similar CLI options by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3706
* Pin playground builds to Python 3.14.6 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3712
* Add .dockerignore to reduce Docker build context by @chernojagne in https://github.com/koxudaxi/datamodel-code-generator/pull/3705
* Bump the github-actions group with 4 updates by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3709
* Preserve array union types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3711
* Add --use-tuple-for-fixed-length-arrays option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3714

## New Contributors
* @chernojagne made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3705

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.72.1...0.72.2

---

## [0.72.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.72.1) - 2026-08-04

## What's Changed
* Add TypedDict total=False option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3689
* Update CHANGELOG for 0.72.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3690
* Support import overrides by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3691
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3692
* Resolve Python type imports by target by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3694
* Support TypeAliasType for non-Pydantic output by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3688
* Bind Python type imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3696
* Structure input model runtime types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3693
* Fix --use-exact-imports for dotted ancestor package case by @bokshitsky in https://github.com/koxudaxi/datamodel-code-generator/pull/3697
* Embed package version at build time by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3699

## New Contributors
* @bokshitsky made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3697

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.72.0...0.72.1

---

## [0.72.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.72.0) - 2026-08-03

## Breaking Changes


### Code Generation Changes
* msgspec empty object default on nested Struct now builds the model - An empty object (`{}`) default whose type is a nested `Struct` reference now renders a conversion factory instead of a plain `dict` factory, changing the runtime default from an empty dict to an actual model instance. No CLI flag is required to trigger this. Regenerating existing schemas that have empty-object defaults for nested Struct fields will produce different output and different default values (#3668)
```python
# Before
nested: Nested | UnsetType = field(default_factory=dict)

# After
nested: Nested | UnsetType = field(default_factory=lambda: convert({}, type=Nested))
```
* Pydantic v2 dataclass optional nested-model default factory - Under `--use-default-factory-for-optional-nested-models`, optional nested Pydantic dataclass fields now emit `Field(default_factory=<Nested>)` where they previously did not, because the field now resolves Pydantic dataclass references. Output for these fields changes on regeneration when that flag is enabled (#3668)
```python
# After (with --use-default-factory-for-optional-nested-models)
nested: Nested | None = Field(default_factory=Nested)
```
* Nested mapping defaults now render as typed constructors - Fields whose type is a generated model (dataclass, msgspec Struct, pydantic dataclass/BaseModel) and whose default is an object/mapping are now emitted as a nested-model constructor default factory instead of a raw dict literal. This changes both the generated source and the runtime default value (a real model instance instead of a `dict`), so regenerating existing schemas can produce different output. The constructor form is only used when every required argument of the nested model is satisfied and the mapping is not recursive; otherwise the previous dict-literal default is preserved. (#3669)
```python
# Before
@dataclass
class Model:
    inner: Inner | None = field(default_factory=lambda: {"v": float("inf")})


# After
@dataclass
class Model:
    inner: Inner | None = field(default_factory=lambda: Inner(v=float("inf")))
```
* Deprecated decorator detection now requires an exact name match - The check that decides whether a `@deprecated` class decorator is already present changed from a text prefix match (`decorator.startswith("@deprecated")`) to an exact unqualified-name match (`is_named_python_decorator(decorator, "deprecated")`). If a schema marks a class as deprecated and you also apply a custom class decorator that merely shares the `deprecated` prefix (e.g. `@deprecated_custom`), the generator previously treated it as the deprecated decorator and skipped emitting one; it now recognizes them as distinct and adds the real `@deprecated('... is deprecated.')` decorator along with the `typing_extensions.deprecated` import. Generated output for these cases changes accordingly. Idempotency for the standard `@deprecated('...')` decorator is unchanged. (#3685)
```python
# Input: deprecated class + custom decorator "@deprecated_custom"

# Before
@deprecated_custom
@dataclass
class LegacyUser: ...


# After
@deprecated_custom
@deprecated("LegacyUser is deprecated.")
@dataclass
class LegacyUser: ...
```

## What's Changed
* Update CHANGELOG for 0.71.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3653
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3654
* Fix parsed source cache scopes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3655
* Simplify XML text loading by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3656
* Isolate cached parsed source values by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3657
* Bump pymdown-extensions from 10.21.3 to 11.0 by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3660
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3659
* Bump urllib3 from 2.6.3 to 2.7.0 by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3661
* Add experimental HTTPX2 backend by @supervirus in https://github.com/koxudaxi/datamodel-code-generator/pull/3662
* Bump the github-actions group with 4 updates by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3665
* Preserve inherited required field types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3664
* Preserve type overrides during model reuse by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3667
* Invalidate semantic model caches by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3666
* Structure generated field rendering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3668
* Unify schema field construction by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3669
* Remove duplicate Pydantic dataclass factory method by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3673
* DOCS ONLY: Update output-model-types.md by @UwU-Parker in https://github.com/koxudaxi/datamodel-code-generator/pull/3672
* Preserve structured enum members by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3683
* Place future imports safely by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3684
* Match decorator targets exactly by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3685
* Structure Python type expressions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3686
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3687

## New Contributors
* @UwU-Parker made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3672

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.71.0...0.72.0

---

## [0.71.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.71.0) - 2026-07-24

## Breaking Changes


### Default Behavior Changes
* New warning emitted by default for unresolved local `$ref` pointers - By default, an unresolved local `$ref` JSON pointer now emits a new `DanglingRefWarning` and generates a fallback `Any` model. Previously such refs resolved to a silent fallback, and some cases (e.g., out-of-range JSON pointer array indices) raised a hard error. Schemas that previously generated cleanly can now produce warnings, which breaks workflows that treat warnings as errors (e.g., `python -W error`). Use `--strict-refs` to fail on unresolved pointers instead, or `--disable-warnings` to silence them (#3639)

### Error Handling Changes
* `$ref` to a non-dict file now raises `InvalidFileFormatError` instead of `TypeError` - Resolving a `$ref` to a JSON/YAML file whose top-level value is not a mapping (e.g., a list) now raises `datamodel_code_generator.InvalidFileFormatError` (a subclass of `Error`/`Exception`) rather than the built-in `TypeError`. Programmatic callers that catch `TypeError` around `generate()` must catch `InvalidFileFormatError` (or `Error`) instead (#3639)
* Malformed-input diagnostics reworded - Malformed inputs that previously surfaced an uncaught traceback now emit a concise message such as `Invalid file format for <type> at <source>: ...`, and the missing-file message changed from `File not found` to `File not found: <path>`. Tooling that matches on the exact previous strings needs updating (#3639)
* Generation now aborts when output paths would overwrite inputs or collide - A new path-conflict validation runs before generation in both the `generate()` Python API and the CLI. Invocations that previously succeeded now raise an `Error` (CLI exits with an error code) in these cases: the output path resolves to an existing input file, the `--emit-model-metadata` path resolves to an existing input file, or the output and model-metadata paths resolve to the same file. Symlinks and hardlinks are resolved before the comparison. Workflows that intentionally wrote output over an input path, or that pointed the model-metadata artifact at the same path as the output, will now fail with one of:
```text
Output path must not overwrite an input path: <path>
Model metadata path must not overwrite an input path: <path>
Output and model metadata paths must be different: <path>
```
(#3647)

## What's Changed
* Update CHANGELOG for 0.70.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3619
* Decouple RootModel dependency ordering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3620
* Improve README and docs homepage links by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3624
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3625
* Move discriminator policies to output models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3621
* Invalidate generation facts for every list mutation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3622
* Document package manager installation options by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3629
* Move dict-key dependency policy to output models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3626
* Decouple parser model behavior helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3632
* Share model constraints across output backends by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3630
* Move Pydantic type rendering to backend by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3627
* Move dataclass ordering policies to output models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3628
* Move template reference metadata to output models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3631
* Declare output model construction capabilities by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3623
* Fix payload runtime validation exclusions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3635
* Cover output backends across input formats by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3636
* Track large schema memory by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3637
* Reuse remote schema connections by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3638
* Improve malformed input diagnostics by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3639
* Bump setuptools from 82.0.1 to 83.0.0 by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3633
* Update project usage examples by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3641
* Fix vLLM usage link by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3642
* Refresh custom template paths by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3640
* List project maintainers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3643
* Preserve Unicode line separators by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3644
* Fix TypedDict unique item sets by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3645
* Defer msgspec forward references by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3646
* Validate generation path conflicts by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3647
* Enforce dynamic model cache size by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3648
* Filter imported dynamic model classes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3649
* Defer nested model default factories by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3650
* Isolate generation state by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3652


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.70.0...0.71.0

---

## [0.70.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.70.0) - 2026-07-23

## Breaking Changes


* Preserved additionalProperties value constraints change generated output - JSON Schema `additionalProperties` (and `propertyNames`/mapping) value schemas that carry constraints (e.g. `minimum`/`maximum`, `minLength`/`maxLength`/`pattern`, array item constraints, `allOf`-merged primitives) now retain those constraints in the generated model instead of dropping them. This produces new `TypeAlias`/`RootModel`/`TypeAliasType` definitions and changes dict value type hints, so output differs for existing schemas. For example, a mapping value that previously generated `dict[str, Literal['fixed']]` now generates a dedicated alias preserving the constraints (#3616):

```python
# Before
class DictModel(RootModel[dict[str, Literal["fixed"]]]):
    root: dict[str, Literal["fixed"]]


# After
DictModelAdditionalProperty = TypeAliasType(
    "DictModelAdditionalProperty",
    Annotated[Literal["fixed"], Field(max_length=100, min_length=1)],
)


class DictModel(RootModel[dict[str, DictModelAdditionalProperty]]):
    root: dict[str, DictModelAdditionalProperty]
```

## What's Changed
* Update CHANGELOG for 0.69.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3613
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3614
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3617
* Preserve additionalProperties value constraints by @chuenchen309 in https://github.com/koxudaxi/datamodel-code-generator/pull/3616
* Rename a msgspec field named "field" to avoid shadowing the field import by @chuenchen309 in https://github.com/koxudaxi/datamodel-code-generator/pull/3615
* Bump the github-actions group with 8 updates by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3618
* Update types-setuptools requirement from <70,>=67.6.0.5 to >=67.6.0.5,<84 by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3584


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.69.0...0.70.0

---

## [0.69.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.69.0) - 2026-07-19

## Breaking Changes


* Discriminated unions with duplicate or unresolvable discriminator values now fall back to a plain union - When a discriminated union contains variants that resolve to the same discriminator value, or variants that are containers, `RootModel`/type-alias wrappers, `None`/`str` members, or otherwise lack a resolvable discriminator literal, the generator no longer emits `Field(..., discriminator='...')` and instead produces a regular union. Previously such schemas emitted a `discriminator=` argument. Users regenerating models from these schemas will see the `discriminator` keyword removed from the affected fields (#3603)

```python
# Before (invalid duplicate-value discriminator was emitted):
class GroupedItem(RootModel[Item | ItemReference]):
    root: Item | ItemReference = Field(..., discriminator="type")


# After (falls back to a plain union when variants are not valid discriminated members):
class MixedItem(RootModel[str | ItemReference | None]):
    root: str | ItemReference | None
```

## What's Changed
* Update CHANGELOG for 0.68.1 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3594
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3595
* Skip unused formatter construction by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3599
* Retain model import caches by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3600
* Fix pydantic typed extra runtime compatibility by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3601
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3605
* Fix legacy pydantic extra templates by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3602
* Handle duplicate discriminator values by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3603
* Bump the github-actions group across 1 directory with 7 updates by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3598
* Allow custom and generated file headers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3607
* Fix payload runtime validation exclusions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3609
* Fix optional discriminator literals by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3608
* Fix invalid dotted model names by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3610
* Add multi-module stdout guard by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3611
* Fix empty ("") property name dropping its alias by @chuenchen309 in https://github.com/koxudaxi/datamodel-code-generator/pull/3612

## New Contributors
* @chuenchen309 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3612

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.68.1...0.69.0

---

## [0.68.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.68.1) - 2026-07-08

## What's Changed
* Skip docs preview deploy for dependabot PRs by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/3576
* Update CHANGELOG for 0.68.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3574
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3575
* Bump the github-actions group across 1 directory with 12 updates by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3567
* Bump python from 3.14.2-slim-bookworm to 3.14.6-slim-bookworm by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3540
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3577
* Delay CLI config imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3579
* Guard builtin formatter tokenization by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3580
* Cache parser regex helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3581
* Cache rendered template field values by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3582
* Speed up unique name lookup by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3583
* Add getting started docs and overview sync by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3586
* Fix ty diagnostics by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3585
* Enforce ty diagnostics by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3588
* Fix type overrides for base refs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3589
* Type generation models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3590
* Annotate ty ignores by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3587
* Type reference sources by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3591
* Use TypeIs for reference models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3592
* Fix pydantic extra annotations by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3593


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.68.0...0.68.1

---

## [0.68.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.68.0) - 2026-07-06

## Breaking Changes


### Error Handling Changes
* Ruff formatting failures now raise instead of being ignored - Ruff subprocess calls previously ran with `check=False` and used their stdout regardless of exit code, so a failed ruff run silently produced (possibly empty or partial) output. `_run_ruff_command` now raises `RuntimeError` when ruff exits non-zero (except the allowed check-with-remaining-diagnostics case), and `_find_ruff_path` raises a `RuntimeError` with an install hint when ruff is missing instead of falling back to `"ruff"`. Workflows that relied on generation silently succeeding despite a ruff error will now fail with an exception. (#3561)

### Code Generation Changes
* `--enable-command-header` now redacts sensitive HTTP options - When `--enable-command-header` is combined with `--http-headers` or `--http-query-parameters`, the reproducibility command line embedded in generated files now writes `<redacted>` in place of those values instead of the literal arguments. Generated file headers change for these flag combinations, so committed output or snapshot tests will differ. (#3561)
* RootModel config rendering for config objects - Pydantic v2 `RootModel` now synchronizes and renders `model_config` derived from config objects (e.g. `regex_engine`, `frozen`) that were previously dropped, and non-dict config objects are now applied in the base model. Generated output for affected pydantic v2 root models may now include a `model_config = ConfigDict(...)` that was not emitted before. (#3561)

## Bug Fixes
* `generate()` keeps `str` inputs as inline source text. Pass a `Path` to `input_` for local file input; if a failed string input resolves to an existing path, `generate()` warns and recommends using `Path`. (#3573)

## What's Changed
* Update CHANGELOG for 0.67.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3556
* Cover llms txt generation check by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3555
* Cache builtin formatter source lines by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3552
* Cover config model force rebuild by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3553
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3557
* Clear imports cache in generation store by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3554
* Cover imports cache mutation contract by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3559
* Shard Windows test jobs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3560
* Shard Python 3.14 coverage job by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3562
* Rebalance CI test shards with measured weights by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3563
* Generate HTML coverage report only on failure by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3564
* Consolidate e2e and check CI jobs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3566
* Speed up Windows CI setup by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3565
* Fix generation reliability edge cases by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3561
* Ignore test fixtures in CodeQL analysis by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3568
* Cache unsupported subinterpreter checks by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3569
* Preload validation dependencies by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3570
* Register snapshot formats once by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3571
* Cache payload codegen sources by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3572
* Keep generate string input inline by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3573


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.67.0...0.68.0

---

## [0.67.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.67.0) - 2026-07-03

## Breaking Changes


### Default Behavior Changes
* Auto input-type CSV fallback is now conditional - When `--input-file-type auto` is used, `infer_input_type()` previously treated **any** YAML parse failure as CSV data. It now falls back to CSV only when the text looks like CSV (non-empty lines with consistent, non-zero comma counts). Inputs that were previously inferred as `csv` — including malformed JSON/YAML and ragged CSV with inconsistent column counts (e.g. `id,name,tel\n1,taro\n`) — will no longer be inferred as CSV. (#3536)
* GitHub Action `extra-args` no longer undergoes shell expansion - The Action previously appended `extra-args` directly into the shell command (`datamodel-codegen "${ARGS[@]}" ${{ inputs.extra-args }}`), so the value was subject to word-splitting, glob expansion, variable expansion, and command substitution. It is now tokenized with Python's `shlex.split`. Workflows that relied on shell features inside `extra-args` (globs like `*.json`, variables like `$HOME`, or command substitution `$(...)`) will no longer have them expanded and must pass literal, individually-quoted arguments instead. (#3542)
* GitHub Action `extras` input is now validated against a whitelist - Previously any value passed to the `extras` input was accepted and injected into the pip install spec. It is now validated against the fixed set `graphql`, `http`, `validation`, `ruff`, `all`; any other value now fails the install step with `::error::Unsupported extras value: <value>` instead of being passed through. Workflows passing extras values outside this set will now error. (#3542)

### Error Handling Changes
* Malformed auto-detected input now raises an error instead of silently inferring CSV - Under `auto` inference, input that fails YAML parsing and does not look like CSV now raises `Error` rather than falling back to CSV and attempting generation. The error message was also expanded to include the underlying YAML parser error, e.g. `Can't infer input file type from the input data. YAML parser error: <Type>: <detail>. Please specify the input file type explicitly with --input-file-type option.` Workflows depending on the old blanket-CSV fallback must now pass `--input-file-type csv` explicitly. (#3536)

## What's Changed
* Update CHANGELOG for 0.66.3 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3515
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3516
* Update supported data type docs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3517
* Render CLI option relationships in docs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3518
* Generate tested docs examples by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3519
* Add CLI option topic metadata foundation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3522
* Sync README support lists by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3523
* Add CLI reference recipe sections by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3520
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3524
* Add Playground option documentation links by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3521
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3525
* Add CLI topic pages by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3526
* Add aggregate docs check by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3527
* Expand tested docs examples by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3528
* Add schema format guide tables by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3529
* Add benchmark scenario guidance by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3531
* Sync README quick-start recipes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3532
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3534
* Add CLI relationship summary by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3530
* Cover parsed source cache parity by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3535
* Fix auto CSV fallback by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3536
* Cover input error messages by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3537
* Refresh Docker image by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3538
* Gate release publishing by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3539
* Harden GitHub Action by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3542
* Document Docker image and action usage by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3544
* Add Docker metadata coverage by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3548
* Gate unused type ignores by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3547
* Assert parsed source cache population by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3546
* Harden action cache key by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3545
* Cover de facto public symbols by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3550
* Correct experimental feature metadata by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3551
* Add generation determinism guard by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3549


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.66.3...0.67.0

---

## [0.66.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.66.3) - 2026-07-01

## What's Changed
* Update CHANGELOG for 0.66.2 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3511
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3512
* Fix scripts module imports in tests by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3514


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.66.2...0.66.3

---

## [0.66.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.66.2) - 2026-07-01

## What's Changed
* Update CHANGELOG for 0.66.1 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3507
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3508
* Add Modular to Used by list by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3509
* Add Pydantic missing sentinel option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3510


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.66.1...0.66.2

---

## [0.66.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.66.1) - 2026-06-30

## What's Changed
* Add schema validators by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3228
* Update CHANGELOG for 0.66.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3502
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3503
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3504
* Fix inline-snapshot compatibility by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3506


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.66.0...0.66.1

---

## [0.66.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.66.0) - 2026-06-26

## Breaking Changes


* msgspec nullable annotated fields now place `None` outside `Annotated` - For `msgspec.Struct` models, nullable fields that carry `Meta`/`Annotated` constraints no longer wrap the type in `Optional` inside the `Annotated[...]`. The `None` member is now emitted as a separate union arm outside the `Annotated` wrapper, which changes the generated type hints and the resulting imports (`Optional` is dropped when no longer needed). This is required for the generated models to validate correctly at runtime under msgspec, but it changes output for existing users. (#3495)

```python
# Before
from typing import Annotated, Optional, Union

name: Union[Annotated[Optional[str], Meta(max_length=5)], UnsetType] = UNSET

# After
from typing import Annotated, Union

name: Union[Annotated[str, Meta(max_length=5)], None, UnsetType] = UNSET
```

## What's Changed
* Update CHANGELOG for 0.65.1 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3492
* Add MCP Python SDK to Used by list by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3494
* Fix msgspec payload runtime compatibility by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3495
* Reorder Used by projects by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3496
* Fix docs workflow zensical invocation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3497
* Bump the github-actions group with 19 updates by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3490
* Automate release benchmark docs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3498
* Create release benchmark data PRs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3500
* Update release benchmark data by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3501


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.65.1...0.66.0

---

## [0.65.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.65.1) - 2026-06-25

## What's Changed
* Update CHANGELOG for 0.65.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3470
* Speed up CLI generation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3471
* Cache JSON Schema ref facts by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3472
* Cache model imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3473
* Add release benchmark docs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3465
* Skip legacy Pydantic union round trip by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3475
* Strip Field None defaults with metadata by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3476
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3478
* Use semantic None default checks by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3479
* Structure field import detection by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3481
* Use structured type rendering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3488
* Use structured SerializeAsAny imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3482
* Use structured x-python-type annotations by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3486
* Cache structured import requirements by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3489
* Fix collapsed root imports in split modules by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3491


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.65.0...0.65.1

---

## [0.65.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.65.0) - 2026-06-21

## Breaking Changes


### Error Handling Changes
* YAML `!!set` tags now rejected - `load_yaml` now validates all YAML input and raises a `yaml.YAMLError` when it encounters the unsupported `tag:yaml.org,2002:set` (`!!set`) tag. Previously such input was loaded silently (constructed as a Python set). YAML schemas or sample data that relied on `!!set` tags will now fail to load with an "Unsupported YAML tag" error. This validation runs unconditionally on every YAML load and is not gated behind any opt-in flag (#3447)

### Code Generation Changes
* msgspec.Struct boolean const fields now generate `bool` instead of `Literal[True]`/`Literal[False]` - When generating `msgspec.Struct` models (`--output-model-type msgspec.Struct`), a JSON Schema `const` with a boolean value now produces a plain `bool` type instead of a boolean `Literal`, because msgspec does not support boolean `Literal` tag values at runtime. Output for other model types (e.g. Pydantic) is unchanged. Existing msgspec users with boolean-const fields will see different generated output. (#3462)
* msgspec.Struct combined object unions now render as tagged Struct unions - For `msgspec.Struct` output, combined (`oneOf`/`anyOf`) object schemas that share a required literal discriminator field with int/str values now have a discriminator inferred and are emitted as tagged `Struct` unions (`tag_field=...`, `tag=...`). This changes the generated class definitions for existing msgspec users whose schemas match this shape. (#3462)

## What's Changed
* Update CHANGELOG for 0.64.1 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3439
* Fix release draft breaking change notes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3440
* Update CHANGELOG for 0.64.1 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3441
* Add inferred union variant names by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3442
* Add model metadata output by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3443
* Add model name mapping by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3444
* Handle nested schema namespaces by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3445
* Resolve external discriminator mappings by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3446
* Unify JSON config loading by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3451
* Add RootModel sequence methods option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3447
* Add dynamic model module names and filtering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3448
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3452
* Skip YAML tag scan without unsupported markers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3454
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3455
* Skip full schema dumps for type constraints by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3456
* Skip typeguard when importing inflect by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3458
* Reuse loaded sources for local refs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3459
* Reduce startup imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3460
* Optimize field import collection by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3461
* Fix msgspec tagged union validation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3462
* Document fast formatter choices by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3463
* Add architecture documentation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3464
* Reuse validated JSON Schema objects by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3466
* Reuse validated schema definitions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3467
* Revert schema object reuse by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3469


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.64.1...0.65.0

---

## [0.64.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.64.1) - 2026-06-19

## Breaking Changes

### Code Generation Changes
* Optional primitive `const` fields no longer emit the const value as an injected default - Optional primitive `const` properties without a schema `default` now render as nullable/omittable (`Literal[...] | None = None`) instead of being populated with the const value when the input key is omitted. Regenerated code and snapshot tests may change. (#3434)

## What's Changed
* Update CHANGELOG for 0.64.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3414
* Fix pydantic v2 dataclass lookaround regex engine by @DarkaMaul in https://github.com/koxudaxi/datamodel-code-generator/pull/3370
* Pass enum schema extensions to templates by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3417
* Fix payload runtime validation exclusions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3418
* Add conformance dashboard by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3423
* Skip legacy dataclass lookaround payloads by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3428
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3425
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3429
* Use shared test helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3424
* Consolidate CLI config fields by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3430
* Guard JSON-pointer list indexing against invalid segments by @DarkaMaul in https://github.com/koxudaxi/datamodel-code-generator/pull/3420
* Add Pydantic v2 alias generator by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3432
* Fix optional primitive const emitting injected non-None default by @tangentlin in https://github.com/koxudaxi/datamodel-code-generator/pull/3434
* Escape generated pyproject TOML strings by @DarkaMaul in https://github.com/koxudaxi/datamodel-code-generator/pull/3436
* Add standard preset by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3422

## New Contributors
* @tangentlin made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3434

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.64.0...0.64.1

---

## [0.64.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.64.0) - 2026-06-14

## Breaking Changes


### Code Generation Changes
* Self-referencing fields are now quoted with `--disable-future-imports` - When `--disable-future-imports` is set (no `from __future__ import annotations` and no native PEP 649 deferred evaluation on Python < 3.14), self-referencing and forward-referencing field annotations in regular `BaseModel` classes are now emitted as quoted forward references instead of bare names. Previously such annotations were left unquoted, producing invalid code that raised `NameError` (Ruff F821) at class-evaluation time. Output for the common case (with `from __future__ import annotations` or Python 3.14 native deferred annotations) is unchanged. Users who snapshot/golden-file generated output for the `--disable-future-imports` configuration with self-referencing models will see the annotation change from unquoted to quoted, e.g. `children: Optional[List[Node]]` → `children: Optional[List["Node"]]`. (#3387)

## What's Changed
* Update CHANGELOG for 0.63.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3345
* Deduplicate module content builder by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3346
* Deduplicate import reference helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3348
* Refactor jsonschema root model registration by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3352
* Refactor XML Schema literal helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3349
* Move builtin formatter helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3351
* Deduplicate Pydantic v2 config helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3350
* Deduplicate DataType type hint rendering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3354
* Fix `constr()` for string fields carrying minItems/maxItems by @DarkaMaul in https://github.com/koxudaxi/datamodel-code-generator/pull/3353
* Cover non-finite import idempotence by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3367
* Deduplicate input text detection by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3357
* Remove stale protobuf coverage pragma by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3358
* Cover explicit null OpenAPI media schemas by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3360
* Simplify Python version feature checks by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3361
* Speed up CI checks by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3378
* Add maintainer link to docs footer and README by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3379
* Use builtin formatter in CI by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3380
* Split coverage by OS by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3381
* Simplify import removal cleanup by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3362
* Pin deprecation warning stacklevel by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3363
* Pin public module exports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3364
* Cover to_hashable branch cases by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3366
* Cover stable toposort behavior by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3369
* Extract registry render helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3371
* Fix minItems for arrays of URI strings by @sjh9714 in https://github.com/koxudaxi/datamodel-code-generator/pull/3377
* Deduplicate config value validators by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3372
* Cover CLI option metadata helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3374
* Cover Pydantic v2 version fallback by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3368
* Fix nullable JSON Schema const enums by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3355
* Pin patchable generation seams by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3365
* Cover utility helper behavior by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3375
* Cover DefaultPutDict behavior by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3376
* Cover validator config normalization by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3373
* Avoid expensive runtime type checks by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3382
* Avoid eager builtin formatter import by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3383
* Avoid eager TOML parser import by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3384
* Stabilize msgspec payload tests by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3385
* Avoid eager input parser imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3386
* Avoid eager parser model imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3388
* Avoid eager AsyncAPI converter imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3389
* Dispose parser on parse errors by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3390
* Fix YAML warning filter handling by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3392
* Cache local parser sources by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3393
* Guard mutable generation inputs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3394
* Compact dynamic cache keys by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3395
* Guard OpenAPI path items by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3397
* Isolate converted schemas by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3396
* Add performance measurement coverage by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3398
* Restore input model modules by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3399
* Reduce Pydantic v2 render work by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3400
* Defer parser config imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3402
* Slot generation facts by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3403
* Fast path JSON schemas by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3404
* Lazy root format exports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3405
* Defer generation refresh by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3406
* Cache parsed sources by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3407
* Shard Python tests by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3408
* Cache payload validation strategies by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3409
* fix: quote self-referencing fields when --disable-future-imports is set by @kaizeenn in https://github.com/koxudaxi/datamodel-code-generator/pull/3387
* Skip discriminator import scan by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3411
* Fast path schema output by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3410

## New Contributors
* @DarkaMaul made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3353
* @sjh9714 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3377
* @kaizeenn made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3387

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.63.0...0.64.0

---

## [0.63.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.63.0) - 2026-06-12

## Breaking Changes


### Default Behavior Changes
* `--check` output now uses POSIX-style paths - File paths in `--check` diagnostic output are now normalized with `Path.as_posix()` instead of `str(path)`. On POSIX systems the output is unchanged, but on **Windows** the diff headers and `MISSING:`/`EXTRA:` lines now use forward slashes (`models/foo.py`) instead of backslashes (`models\foo.py`). Tooling or snapshot tests that parse `--check` output on Windows may need updating. (#3287)

## What's Changed
* Update CHANGELOG for 0.62.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3313
* Add generate prompt JSON output by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3285
* Strengthen test oracles by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3314
* Cover pragma targets by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3315
* Consolidate test infrastructure helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3316
* Remove dead branches and import cleanups by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3318
* Block embedded IPv4 SSRF bypasses by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3319
* Add generation JSON output format by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3287
* Remove no-op overrides by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3320
* Initialize parser lazy state explicitly by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3321
* Break parser-model import cycles by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3322
* Document LLM option workflow by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3284
* Improve generate prompt guidance by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3286
* Add experimental agent skill by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3310
* Consolidate JSON Schema data formats by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3323
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3326
* Deduplicate model-layer helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3327
* Deduplicate JSON Schema parser helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3328
* Deduplicate parser base helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3329
* Deduplicate parser converter helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3330
* Use resolved converted source cache keys by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3332
* Add JSON Schema suite conformance by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3333
* Clarify payload adapter error by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3334
* Add OpenAI sponsor logo by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3338
* Add payload rejection oracle by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3335
* Expand payload backend validation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3337
* Add payload round trip validation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3339
* Add payload runtime matrix by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3340
* Expand payload backend matrix by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3341
* Fix Pydantic 2.0 fallbacks by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3343
* Refactor generate flow by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3344


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.62.0...0.63.0

---

## [0.62.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.62.0) - 2026-06-10

## Breaking Changes


### Code Generation Changes
* msgspec: Required nullable fields no longer receive a spurious default - Previously, required fields with nullable types (e.g., `str | None`) in msgspec Structs were incorrectly rendered with `= None`. They are now correctly rendered without a default assignment (e.g., `label: str | None` instead of `label: str | None = None`), which means callers must provide the value explicitly (#3292)
* msgspec: Array length constraints now render as Meta annotations - `minItems`/`maxItems` schema constraints on array fields were previously silently ignored for msgspec output. They now render as `Annotated[list[T], Meta(min_length=..., max_length=...)]` when `--use-annotated` is enabled (e.g., `list[Pet]` becomes `Annotated[list[Pet], Meta(max_length=10, min_length=1)]`) (#3292)
* msgspec: Inherited Structs with field ordering conflicts now use `kw_only=True` - When a child Struct inherits optional fields from a parent and adds required fields, the child now renders with `kw_only=True` as a base class kwarg (e.g., `class Child(Base, kw_only=True):`), changing the calling convention to keyword-only arguments (#3292)
* Non-finite float values now use `float()` calls instead of `math` imports - Generated code previously rendered `inf`, `-inf`, and `nan` (with `from math import inf, nan`). Now renders `float('inf')`, `float('-inf')`, and `float('nan')` without the `math` import. Semantically equivalent but changes generated output. (#3289)
* Decimal constraint arguments now use `Decimal()` objects - `condecimal()` constraints previously used bare int/float values (e.g., `condecimal(ge=0, le=1000)`). Now uses `Decimal` objects (e.g., `condecimal(ge=Decimal('0'), le=Decimal('1000'))`) with an added `from decimal import Decimal` import in generated code. (#3289)
* Integer fields with fractional constraints are normalized to integer-safe bounds - Fractional constraint values on integer types are now converted: `gt: 0.5` becomes `ge: 1`, `lt: 2.5` becomes `le: 2`, and `multiple_of: 1` is dropped entirely. Previously these fractional values were passed through as-is to `conint()` or `Field()`. (#3289)
* msgspec array length constraints are now rendered - `min_items` and `max_items` schema constraints on array fields are now emitted as `Meta(min_length=..., max_length=...)` annotations in msgspec output. Previously these constraints were silently ignored. (#3289)
* msgspec required nullable fields no longer get default `= None` - Required nullable fields in msgspec Structs previously received a `= None` default assignment. Now they are rendered without a default, requiring callers to provide the value explicitly. (#3289)
* TypedDict `extra_items` now uses direct identifiers instead of string forward references - When `additionalProperties` references another type, the generated `extra_items=` keyword argument changed from a quoted string to a bare identifier (e.g., `extra_items='BaseExtra'` → `extra_items=BaseExtra`). The generated code remains valid because files include `from __future__ import annotations`, but the textual output differs (#3301)
* Functional-syntax TypedDict description placement changed - Description docstrings for functional-syntax TypedDicts (using `TypedDict('Name', {...})`) are now rendered **before** the definition instead of after it. Previously the docstring appeared as a string literal following the assignment; it now appears as a comment block preceding it (#3301)
* UUID2 format now generates `uuid.UUID` instead of `pydantic.UUID2` - Schemas using `"format": "uuid2"` now generate `UUID` imported from the `uuid` standard library instead of the non-existent `UUID2` from `pydantic`. This is a bug fix since `pydantic.UUID2` was never a valid Pydantic v2 type (the old generated code would raise `ImportError`), but the generated import and type annotation will differ for any schema using this format (#3297)

### Custom Template Update Required
* msgspec.jinja2 default assignment condition simplified - The Jinja2 template condition for rendering default values was changed from `not field.field and (not field.required or field.use_default_with_required or field.data_type.is_optional or field.nullable)` to `not field.field and (not field.required or field.use_default_with_required)`. Users with custom msgspec templates that replicate the old logic should update accordingly (#3292)
* msgspec.jinja2 template default value condition changed - The condition for rendering default value assignments changed from `not field.required or field.use_default_with_required or field.data_type.is_optional or field.nullable` to `not field.required or field.use_default_with_required`. Custom templates derived from the built-in msgspec template may need to be updated. (#3289)
* `TypedDictFunction.jinja2` template restructured - The built-in functional TypedDict template moved the `description` block from after the class definition to before it and added support for field-level docstrings. Users with custom templates that were based on or extend this template may need to update accordingly (#3301)

### Error Handling Changes
* XML Schema schemaLocation references outside the input base path are now blocked - Any `xs:include`, `xs:import`, `xs:redefine`, or `xs:override` with a `schemaLocation` that resolves outside the input base directory now raises an unconditional error. Previously, these references were silently resolved regardless of location. Users with XML schemas that include files from parent or sibling directories must move the included schemas under the input directory before generating models. (#3308)

### Default Behavior Changes
* Local JSON Schema `$ref` references outside the input base path now emit deprecation warnings - Relative path refs (e.g., `$ref: "../other/schema.json"`) and `file://` URL refs that resolve outside the input base directory now emit a `FutureWarning` under the default behavior (no `--allow-remote-refs` flag). Previously these were silently resolved. Pass `--allow-remote-refs` to suppress the warning, or move referenced schemas under the input directory. (#3308)
* `--no-allow-remote-refs` now blocks `file://` URLs and local refs outside the base path - Previously `--no-allow-remote-refs` only blocked HTTP(S) `$ref` fetching. It now also blocks `file://` URL references and relative local `$ref` references that resolve outside the input base path. Users who rely on `file://` refs or out-of-tree local refs with `--no-allow-remote-refs` must switch to `--allow-remote-refs` or restructure their schemas. (#3308)

## What's Changed
* Add security policy by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3283
* Update CHANGELOG for 0.61.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3282
* Optimize DataType prototype copying and import collection by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3291
* Cache dynamically created context DataType classes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3294
* Reuse ModelResolver for field name collision resolution by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3295
* Reduce redundant Field() data computation in pydantic fields by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3293
* Fix raw input parsing by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3290
* Fix msgspec field ordering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3292
* Fix pydantic constraint rendering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3289
* Break reference cycles in parsed graph after generation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3298
* Compute field statement check once when collecting imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3296
* Cache ref schema facts in get_ref_data_type by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3301
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3300
* Fall back uuid2 format to uuid.UUID by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3305
* Iterate the smaller side in transform_kwargs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3297
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3307
* Fix msgspec constraint and default rendering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3302
* Avoid building unused Field strings and empty extras dicts by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3304
* Fix TypedDict functional descriptions and extra_items forward references by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3306
* Fix playground options layout by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3308
* Improve private network option help by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3309
* Fix enum member default resolution by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3303
* Use Claude Opus 4.8 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3312
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3311


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.61.0...0.62.0

---

## [0.61.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.61.0) - 2026-06-08

## Breaking Changes

* HTTP(S) schema fetching now blocks localhost, loopback, private, link-local, reserved, and other non-public network targets by default. Users who intentionally fetch schemas from trusted internal/private network endpoints must pass `--allow-private-network` or set `allow_private_network=True`.
* HTTP(S) redirects are now followed manually and each redirect target is validated before fetching. Redirects to non-public network targets are blocked unless private network access is explicitly enabled.

## Security

* Fixed SSRF protections for HTTP(S) schema fetching via `--url` and remote JSON Schema/OpenAPI `$ref` URLs. (GHSA-rfr2-mq9m-x2qx, GHSA-954p-556p-r752)
* Remote `$ref` fetching remains controlled by `--allow-remote-refs`; non-public remote references additionally require `--allow-private-network`.

## What's Changed
* Update CHANGELOG for 0.60.2 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3277
* Fix generated files by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3279
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3280


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.60.2...0.61.0

---

## [0.60.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.60.2) - 2026-06-08

## Security

* Fixed code injection from schema-provided `default_factory` values. (GHSA-386q-5hp3-95m9)
* Fixed code injection from JSON Schema `x-python-type` extension values. (GHSA-m34r-v34r-rf9q)
* Fixed code injection from `comment` entries supplied through `--extra-template-data`. (GHSA-wjv6-jcfj-mf9r)
* Fixed code injection from Pydantic v2 validator definitions supplied through `--validators` or `--extra-template-data`. (GHSA-8m8r-38jm-f355)

## What's Changed
* Update CHANGELOG for 0.60.1 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3274
* Cover validator error formatting by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3276
* Update CHANGELOG for 0.60.1 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3275


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.60.1...0.60.2

---

## [0.60.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.60.1) - 2026-06-07

## Security

* Fixed code injection from GraphQL union descriptions containing carriage returns. (GHSA-j884-q54q-mmx3)

## What's Changed
* Update CHANGELOG for 0.60.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3269
* Fix readOnly/writeOnly option docs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3270
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3271
* Fix built-in formatter type alias union by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3273


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.60.0...0.60.1

---

## [0.60.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.60.0) - 2026-06-04

## Breaking Changes

* Avro record field defaults are no longer emitted as generated Python defaults. Avro defaults describe reader behavior, not Python model construction defaults, so generated Avro model fields now stay required even when the Avro schema has `default`. This also applies to Avro schemas embedded in AsyncAPI multi-format schemas. (#3256)
* XML Schema datatype mappings were corrected. `xs:decimal` now generates `Decimal` instead of `float`; `xs:dateTime` defaults to standard-library `datetime` because XML Schema allows values without a timezone; `xs:dateTimeStamp` remains `AwareDatetime`; `xs:duration` and `xs:yearMonthDuration` now generate `str`, while `xs:dayTimeDuration` still generates `timedelta`. (#3248)
* GraphQL input fields with schema defaults are now generated as optional fields with defaults. For example, non-null list inputs declared with `= []` can now be omitted when constructing the generated model. (#3255)
* Protobuf `bytes` defaults are now generated as bytes literals instead of strings, including escaped byte sequences. (#3252)
* XML Schema default and fixed values are parsed according to their XSD lexical types. This affects list defaults, union defaults, non-finite float values, temporal defaults, boolean defaults with XML Schema whitespace, and fixed decimal values. (#3258, #3260, #3261, #3263, #3267, #3268)

## Notes

* `--output-datetime-class` is respected for XML Schema `xs:dateTime` and `xs:dateTimeStamp` when explicitly provided. Without the option, the XML Schema defaults above are used. (#3266)
* Enum member defaults remain opt-in through `--set-default-enum-member`. The default behavior is not changed to force enum member references. (#3264)
* JSON Schema and OpenAPI `const` values are not treated as generated Python defaults unless the schema also defines a default. XML Schema `fixed` keeps its XSD value-constraint behavior. (#3268)

## What's Changed

* Update CHANGELOG for 0.59.1 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3239
* Fix release notification duplicates by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3240
* Respect OpenAPI 3.2 feature gates by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3243
* Enforce assert helper usage by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3244
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3246
* Fix PyPI badge rendering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3247
* Preserve AsyncAPI Avro defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3242 (superseded by #3256 before release)
* Fix XML Schema datatypes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3248
* Fix Avro field defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3256
* Fix GraphQL input defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3255
* Fix Protobuf bytes defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3252
* Fix Protobuf enum defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3253
* Fix XML Schema list defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3258
* Fix XML Schema union defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3260
* Fix XML Schema float defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3261
* Fix complex const defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3259 (narrowed by #3268 before release)
* Fix GraphQL enum defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3262 (default enum-member behavior restored by #3264 before release)
* Restore enum default option behavior by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3264
* Add conversion diagram by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3257
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3265
* Fix XML Schema datetime class override by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3266
* Fix XML Schema temporal defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3263
* Fix XML Schema boolean whitespace by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3267
* Fix const default scope by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3268

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.59.1...0.60.0

---

## [0.59.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.59.1) - 2026-06-03

## What's Changed
* Update CHANGELOG for 0.59.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3216
* Update supported input formats by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3219
* Fix docs preview deploy by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3218
* Update zensical by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3220
* Add builtin formatter warning by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3178
* Add playground by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3217
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3222
* Add playground state URLs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3223
* Add playground version selector by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3224
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3225
* Optimize playground runtime warmup by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3227
* Fix playground runtime versions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3229
* Add playground privacy note by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3231
* Fix download badge by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3232
* Add OpenAPI 3.2 schema version by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3233
* Add MCP tools input type by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3234
* Parse AsyncAPI multi-format schemas by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3236
* Allow leading underscore class names by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3237
* Clarify uv installation guidance by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3238


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.59.0...0.59.1

---

## [0.59.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.59.0) - 2026-05-29

## Breaking Changes

### Code Generation Changes
* Pattern key constraints now include `propertyNames` constraints - When a JSON Schema uses both `patternProperties` and `propertyNames`, the generated dict key type now merges constraints from both. Previously, `propertyNames` constraints such as `minLength`, `maxLength`, and `$ref` could be ignored when `patternProperties` was present. Regenerated code may produce stricter key types and reject data that was previously accepted by less-strict generated models. (#3192)
* Typed `additionalProperties` now generates a typed `__pydantic_extra__` field for Pydantic v2 models - When a JSON Schema defines schema-valued `additionalProperties`, generated Pydantic v2 models now include `__pydantic_extra__: dict[str, <type>]`. Previously, these models only allowed extra fields without typing their values. This changes generated output and makes Pydantic validate extra field values at runtime. (#3205)
* Dataclass field ordering with `--use-annotated` may change - Dataclass field assignment detection now accounts for constraints moved into `Annotated[...]`. Generated dataclass fields may be reordered so fields without defaults come before fields with defaults. (#3203)

### Default Behavior Changes
* Auto-detection (`--input-file-type auto`) now recognizes AsyncAPI, Avro, and Protocol Buffers inputs - Inputs that previously fell back to JSON/YAML handling or failed detection may now be detected as one of these formats. (#3194, #3195, #3198)

### Compatibility Notes
* The temporary Pydantic minimum-version change from `pydantic>=2,<3` to `pydantic>=2.6,<3` was reverted before this release. The final dependency range for Python < 3.14 remains `pydantic>=2,<3`. (#3210, #3215)

## What's Changed
* Update CHANGELOG for 0.58.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3187
* Add deprecation registry and docs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3188
* Add XML Schema parser by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3183
* Fix null-only type arrays by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3189
* Fix contains default minContains by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3190
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3193
* Fix fixed prefixItems tuples by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3191
* Fix pattern key constraints by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3192
* Fix propertyNames ref constraints by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3197
* Add Apache Avro schema input type by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3195
* Refactor JSON Schema parser helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3199
* Document Avro input support by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3200
* Use expected files for schema e2e tests by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3201
* Add Protocol Buffers input support by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3198
* Add AsyncAPI input file type by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3194
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3204
* Fix dataclass field ordering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3203
* Add generated header marker option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3208
* Fix path-absolute local refs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3207
* Fix typed additionalProperties extras by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3205
* Require Pydantic 2.6 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3210
* Add experimental feature list by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3211
* Fix scoped type-overrides changing more fields then intended by @supervirus in https://github.com/koxudaxi/datamodel-code-generator/pull/3213
* Copy mapped data types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3214
* Support Pydantic 2.5 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3215

## New Contributors
* @supervirus made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3213

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.58.0...0.59.0

---

## [0.58.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.58.0) - 2026-05-25

## New Features

* Added `--serialization-aliases` for Pydantic v2 serialization alias mapping. (#3146)
* Added `--openapi-include-info-version` to emit `OPENAPI_INFO_VERSION` from OpenAPI `info.version`. (#3176)
* Added `--use-object-type` to generate `object` instead of `Any` for unspecified JSON Schema object and array values. (#3177)

## Breaking Changes

### Code Generation Changes
* Duplicate validation aliases are now deduplicated in generated Pydantic v2 `AliasChoices` - Fields that previously generated duplicate entries such as `AliasChoices('endDate', 'end_date', 'endDate')` now generate each alias once. Runtime behavior is equivalent, but exact generated output changes. (#3146)
* JSON Schema edge-case fixes change generated output for affected schemas - Schema-valued `additionalProperties`/`unevaluatedProperties`, `const: null`, complex enum values, all-false `patternProperties`, non-string `propertyNames`, boolean array item schemas, `contains`, `minProperties`/`maxProperties`, and enum references through `allOf` now generate more accurate annotations or constraints. Users with snapshots or exact-output checks may see diffs. (#3167)
* Duplicate TypedDict import fix changes generated output - Mixed closed/open TypedDict generation no longer imports `TypedDict` from both `typing` and `typing_extensions`; `TypedDict` is kept only where required. (#3155)
* Schema-derived payload fixes change generated output for affected schemas - Forward-referenced dict key unions, indirect `$ref` types in `allOf`, `additionalProperties` with `$ref`, heterogeneous root constraints, and unresolved discriminator fields now generate more valid types/fields. (#3168)
* Primitive `allOf` schemas now generate root-style payload types - Primitive-only `allOf` and top-level `allOf` combined with `oneOf`/`anyOf` now generate `RootModel`/root payload types instead of empty or object-like models. Code instantiating the previous generated classes may need updates. (#3169, #3171)
* Discriminator literal generation changed for some enum mappings - Discriminator mappings now use the resolved literal values for enum-backed mappings, including non-string values where applicable. (#3074, #3170)
* `multipleOf` intersections in `allOf` now use the least common multiple - For example, `multipleOf: 5` combined with `multipleOf: 10` now generates `multiple_of=10` instead of incorrectly keeping the first value. Decimal multiples are handled similarly. (#3172)
* Schema metadata fields are now preserved automatically - `contentEncoding`, `contentMediaType`, `contentSchema`, `externalDocs`, and `xml` are included in generated `json_schema_extra` when present in the input schema, even without `--model-extra-keys`. (#3175)

### Error Handling Changes
* `--output-datetime-class` now rejects incompatible TypedDict and Dataclass output combinations - Pydantic-specific datetime classes with `typing.TypedDict`, and incompatible dataclass API usage, now raise errors instead of silently producing fallback output. (#3155, #3169)
* Boolean `false` inside `allOf` now raises `SchemaParseError` - Unsatisfiable `allOf` branches are reported instead of generating incorrect models. `false` branches in `oneOf`/`anyOf` are filtered where appropriate. (#3168)

## What's Changed
* Create changelog updates through PRs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3138
* Add manual changelog update trigger by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3139
* Update CHANGELOG for 0.57.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3140
* Normalize changelog release body spacing by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3141
* Fix duplicate changelog prepend by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3143
* Update CHANGELOG for 0.57.0 by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3144
* Add serialization aliases by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3146
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3148
* Add generation dependency index by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3154
* Document boundary payload typing by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3162
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3163
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3164
* Improve code generation performance by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3166
* Map OpenAPI Discriminator mapping's values to their real value in enums  by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/3074
* Fix JSON Schema edge-case generation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3167
* fix(TypedDict): Issue with DateTime options not being used in the output, and duplicate TypedDict imports by @richinfante in https://github.com/koxudaxi/datamodel-code-generator/pull/3155
* Add schema-derived payload validation tests by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3168
* Fix msgspec enum discriminator tag by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3170
* Fix top-level allOf oneOf schema by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3171
* Fix primitive allOf payload types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3169
* Fix allOf multipleOf intersection by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3172
* Align schema feature support status by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3174
* Support additional JSON Schema string formats by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3173
* Add OpenAPI info version constant by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3176
* Preserve schema metadata extras by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3175
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3180
* Add object type option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3177
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3181

## New Contributors
* @richinfante made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3155

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.57.0...0.58.0

---

## [0.57.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.57.0) - 2026-05-07

## Breaking Changes


### Code Generation Changes
* `--use-default` no longer makes required fields nullable - Previously, `--use-default` turned required fields into optional nullable fields (e.g., `status: str | None = 'active'`). Now required fields keep their original non-nullable type and just get the default value rendered (e.g., `status: str = 'active'`). Users whose downstream code depends on these fields being `Optional`/nullable will need to update. (#3054)
* Required model-ref fields no longer render defaults without `--use-default` - Previously, required fields referencing models (e.g., `shipping_address: Address`) inconsistently rendered defaults with `validate_default=True` while scalar required fields did not. Now all required fields consistently omit defaults unless `--use-default` is passed. Users who relied on the previous behavior where model-ref required fields had defaults rendered will see those defaults removed. (#3054)

### Custom Template Update Required
* Built-in Jinja2 templates now use `field.use_default_with_required` - The built-in templates for `BaseModel`, `dataclass`, `pydantic_v2/dataclass`, and `msgspec` were updated to check `field.use_default_with_required` alongside `field.required` when deciding whether to render defaults. Custom templates that replicate the old default-rendering logic (e.g., `{%- if not field.required %}`) will still work but won't support the new `--use-default` behavior for required fields. To get the updated behavior, custom templates should change conditions like `not field.required` to `(not field.required or field.use_default_with_required)`. (#3054)

## What's Changed
* Harden workflow credentials by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3095
* Fix release automation workflows by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3110
* Enforce shared assertions in e2e tests by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3108
* Fix docs preview required check by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3112
* Fix required field default rendering and --use-default nullable types by @butvinm in https://github.com/koxudaxi/datamodel-code-generator/pull/3054
* Remove unused CLI doc schema version lookup by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3113
* Fix byte to binary type mapping by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3114
* Create generated docs sync PRs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3117
* Support local HTTP ref paths by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3116
* Fix reuse discriminator literals by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3115
* docstrings that can be single line to be formatted on a single line by @kevin-paulson-mindbridge-ai in https://github.com/koxudaxi/datamodel-code-generator/pull/3107
* Fix indefinite hang on OpenAPI schemas with cyclic model dependencies by @kevin-paulson-mindbridge-ai in https://github.com/koxudaxi/datamodel-code-generator/pull/3078
* Add OpenAPI enum literal alias regression test by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3124
* Fix pydantic model extra warnings by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3127
* Fix snake case array discriminator by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3125
* Fix serialization alias choices by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3126
* Fix generated docs sync prompt snapshots by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3129
* Add manual generated docs sync trigger by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3130
* Use source tree for generated prompt snapshots by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3132
* Propagate enum member descriptions for anyOf const pattern by @mvanhorn in https://github.com/koxudaxi/datamodel-code-generator/pull/3133
* Allow generated prompt snapshot updates by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3134
* Write generated prompt snapshots directly by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3135
* Preserve tox python preference for generated docs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3136
* Sync generated docs by @dcg-generated-docs[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3137

## New Contributors
* @mvanhorn made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3133
* @dcg-generated-docs[bot] made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3137

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.56.1...0.57.0

---

## [0.56.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.56.1) - 2026-04-16

## What's Changed
* Fix `--base-class-map` and `--enum-field-as-literal-map` long inline json support by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/3075
* Prefer CLI input over pyproject url by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3083
* Fix relative URL refs with path-only root ids by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3085
* tomli was merged under the name tomllib into std library *3.11* by @a-detiste in https://github.com/koxudaxi/datamodel-code-generator/pull/3088
* Fix root model reuse collapse by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3089
* Docs: describe --keep-model-order as deterministic dependency-aware ordering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3090

## New Contributors
* @a-detiste made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3088

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.56.0...0.56.1

---

## [0.56.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.56.0) - 2026-04-04

## Breaking Changes

### Code Generation Changes
* Generated default field syntax changed - Fields with structured defaults (dicts, lists, model references) now use `Field(default_value, validate_default=True)` instead of `default_factory=lambda: TypeAdapter(...).validate_python(...)` or `default_factory=lambda: Model.model_validate(...)`. This produces simpler, more readable code but changes the generated output format. (#3050)
* TypeAdapter import removed from generated code - Generated models no longer import `TypeAdapter` from pydantic since `validate_default=True` handles validation natively. (#3050)
* Default value handling for model-referencing fields rewritten - Fields with defaults referencing Pydantic models (BaseModel, RootModel, type aliases) now generate `Field(<raw_value>, validate_default=True)` instead of `default_factory=lambda: Model.model_validate(...)`, `default_factory=lambda: TypeAdapter(...).validate_python(...)`, or `default_factory=lambda: Model(...)`. Empty collection defaults changed from `default_factory=list`/`default_factory=dict` to `Field([], validate_default=True)`/`Field({}, validate_default=True)`. The generated code is semantically equivalent under Pydantic v2 but textually different, which will break snapshot tests or tooling that matches exact output. `pydantic.TypeAdapter` is no longer imported in generated code. (#3070)
* Default values for model-referencing fields now use `validate_default=True` instead of `default_factory` lambdas - Fields with structured defaults (dicts, lists, or scalars referencing Pydantic models/RootModels) previously generated `default_factory=lambda: ModelName.model_validate(value)` or `default_factory=lambda: ModelName(value)`. They now generate `Field(value, validate_default=True)`, producing simpler but different output. Empty collection defaults changed from `default_factory=list`/`default_factory=dict` to `Field([], validate_default=True)`/`Field({}, validate_default=True)`. Users who regenerate code will see different output. (#3071)
  Before:
  ```python
  count: CountType | None = Field(default_factory=lambda: CountType(10))
  items: dict[str, Item] | None = Field(default_factory=dict, title="Items")
  ```
  After:
  ```python
  count: CountType | None = Field(10, validate_default=True)
  items: dict[str, Item] | None = Field({}, title="Items", validate_default=True)
  ```
* Default values for fields referencing models now use `validate_default=True` instead of `default_factory=lambda:` - Fields with structured defaults (dicts/lists) that reference Pydantic models previously generated `default_factory=lambda: Model.model_validate(...)` or `default_factory=lambda: TypeAdapter(Type).validate_python(...)` patterns. They now generate the raw default value directly with `validate_default=True` (e.g., `Field({'key': 'val'}, validate_default=True)` instead of `Field(default_factory=lambda: Model.model_validate({'key': 'val'}))`). This changes the generated code output and may affect users who depend on the exact generated code structure, pin generated output in tests, or use custom post-processing. The runtime behavior should be equivalent for Pydantic v2 users. (#3072)
* `TypeAdapter` import removed from generated code - Generated code no longer imports `pydantic.TypeAdapter` for default value handling. Code that previously used `TypeAdapter(...).validate_python(...)` in default factories now uses inline defaults with `validate_default=True`. (#3072)
* Integer and boolean discriminator values now supported in generated Literal types - Discriminator fields previously only generated string literal values. They now support `int` and `bool` discriminator values (e.g., `Literal[1]` instead of `Literal['1']`), which changes generated code for schemas using integer discriminator mappings. (#3072)

### API/CLI Changes
* `ValidatedDefault` and `WrappedDefault` classes removed - These internal classes were exported from `datamodel_code_generator.model.base` and have been removed. Code importing these types will break:
  ```python
  # Before (broken)
  from datamodel_code_generator.model.base import ValidatedDefault, WrappedDefault
  ```
  (#3050)
* `SUPPORTS_WRAPPED_DEFAULT` and `SUPPORTS_VALIDATED_DEFAULT` class variables removed - These flags were removed from the `DataModel` base class. Custom model classes that override these variables will see attribute errors. (#3050)
* Internal types `ValidatedDefault` and `WrappedDefault` removed - The `datamodel_code_generator.model._types` module was deleted and `ValidatedDefault`/`WrappedDefault` are no longer exported from `datamodel_code_generator.model.base`. Code that imports or subclasses these types will break. The `SUPPORTS_WRAPPED_DEFAULT` and `SUPPORTS_VALIDATED_DEFAULT` class variables were removed from `DataModel` and its subclasses; custom model classes referencing these attributes will need updating. (#3070)
* Removed `WrappedDefault`, `ValidatedDefault` classes and `SUPPORTS_WRAPPED_DEFAULT`, `SUPPORTS_VALIDATED_DEFAULT` class variables - The `WrappedDefault` and `ValidatedDefault` classes from `datamodel_code_generator.model._types` (re-exported via `datamodel_code_generator.model.base`) have been deleted. The `DataModel` class variables `SUPPORTS_WRAPPED_DEFAULT` and `SUPPORTS_VALIDATED_DEFAULT` have also been removed. Code that imports or references these will break. (#3071)
* New `--allow-remote-refs` / `--no-allow-remote-refs` CLI option and `allow_remote_refs` config field - Remote `$ref` fetching over HTTP/HTTPS now emits a deprecation warning by default. Pass `--allow-remote-refs` to suppress the warning, or `--no-allow-remote-refs` to block remote fetching entirely. In a future version, remote fetching will be disabled by default. Users relying on remote `$ref` resolution should add `--allow-remote-refs` to their invocations to avoid the deprecation warning and prepare for the future default change. (#3072)
* New `SchemaFetchError` exception for HTTP fetch failures - Remote schema fetching now raises `SchemaFetchError` (instead of propagating raw `httpx` exceptions) on HTTP errors, non-2xx status codes, or unexpected HTML responses. Users catching specific `httpx` exceptions from remote ref resolution will need to catch `SchemaFetchError` instead. (#3072)

### Error Handling Changes
* Missing local `$ref` now raises `Error` instead of `FileNotFoundError` - Previously, when a `$ref` pointed to a non-existent local file, a raw `FileNotFoundError` propagated to callers. Now it raises `datamodel_code_generator.Error` with the message `"$ref file not found: <path>"`. Programmatic users catching `FileNotFoundError` specifically will need to catch `Error` instead (#3051)
* HTTP fetch failures now raise `SchemaFetchError` instead of propagating raw exceptions - HTTP errors (4xx/5xx status codes), unexpected HTML responses, and transport errors (DNS, timeout, connection) that previously resulted in downstream YAML/JSON parse errors or raw `httpx` exceptions now raise `SchemaFetchError` (a subclass of `Error`) before parsing is attempted. Users catching specific parse errors or `httpx` exceptions for these scenarios will need to update their error handling (#3051)
* HTTP fetch errors now raise `SchemaFetchError` instead of raw httpx exceptions - The `get_body()` function in `http.py` now catches HTTP errors and raises `SchemaFetchError` (a new `Error` subclass) for HTTP status >= 400, network failures, and unexpected HTML responses. Code that caught raw `httpx` exceptions from remote schema fetching will need to catch `SchemaFetchError` instead. (#3071)
* Remote `$ref` fetching now emits `FutureWarning` without `--allow-remote-refs` - Fetching remote HTTP/HTTPS `$ref` references without explicitly passing `--allow-remote-refs` now emits a `FutureWarning` deprecation warning. In a future version, remote fetching will be disabled by default. Users relying on implicit remote ref fetching should add `--allow-remote-refs` to suppress the warning. (#3071)
* HTTP fetch errors now raise `SchemaFetchError` with validation of response content type - Previously, fetching a remote `$ref` that returned an HTML error page would silently pass the HTML through as schema content. Now it raises `SchemaFetchError` if the response has `text/html` content type or a 4xx/5xx status code. This may cause previously-silent failures to become loud errors. (#3072)

### Default Behavior Changes
* Implicit remote `$ref` fetching now emits `FutureWarning` - When a `$ref` resolves to an HTTP(S) URL and `--allow-remote-refs` is not explicitly passed, the tool still fetches the remote reference but emits a `FutureWarning`. This may cause failures in environments running with `-W error` (warnings as errors) or strict warning filters. Pass `--allow-remote-refs` explicitly to suppress the warning (#3051)
* Remote `$ref` fetching now emits a `FutureWarning` - When the parser encounters an HTTP/HTTPS `$ref` without `--allow-remote-refs` being explicitly set, a `FutureWarning` is emitted warning that remote fetching will be disabled by default in a future version. Pass `--allow-remote-refs` to silence the warning, or `--no-allow-remote-refs` to block remote fetching immediately. (#3070)

### Custom Template Update Required
* Type alias templates updated with `fields` guard - All six type alias templates (`TypeAliasAnnotation.jinja2`, `TypeAliasType.jinja2`, `TypeStatement.jinja2`, `UnionTypeAliasAnnotation.jinja2`, `UnionTypeAliasType.jinja2`, `UnionTypeStatement.jinja2`) now wrap the main body in `{% if fields %}...{% else %}` blocks that fall back to `{{ base_class }}` when no fields are present. Users with custom copies of these templates must add the same guard or handle the empty-fields case. (#3070)
* Type alias Jinja2 templates now require `fields` guard and `base_class` fallback - The built-in templates `TypeAliasAnnotation.jinja2`, `TypeAliasType.jinja2`, `TypeStatement.jinja2`, and their Union variants now wrap field access in `{%- if fields %}...{%- else %}` blocks with a `base_class` fallback for empty field lists. Users with custom templates derived from the old versions will need to add similar guards. (#3071)
* Type alias templates (`TypeAliasAnnotation.jinja2`, `TypeAliasType.jinja2`, `TypeStatement.jinja2`, and their Union variants) now handle an empty `fields` list with a fallback to `base_class` - If you have custom copies of these templates, they need to be updated to include the new `{%- if fields %}...{%- else %}...{%- endif %}` branching logic. Without this update, custom templates may error when `fields` is empty. (#3072)

## What's Changed
* Use validate_default=True for structured union defaults by @butvinm in https://github.com/koxudaxi/datamodel-code-generator/pull/3050
* Switch download badge from pepy.tech to pypistats.org by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3052
* Update projects list with notable AI/LLM ecosystem projects by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3053
* Add ryaml as optional YAML backend for faster parsing by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3055
* Update release draft model and preserve breaking changes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3057
* Add --allow-remote-refs to disable HTTP fetching of $ref by default by @butvinm in https://github.com/koxudaxi/datamodel-code-generator/pull/3051
* Fix type alias template crash with reuse_model on empty fields by @butvinm in https://github.com/koxudaxi/datamodel-code-generator/pull/3060
* Fix non-string OpenAPI discriminator literals by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3070
* Support JSON files for mapping options by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3071
* Refresh project usage list by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3072


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.55.0...0.56.0

---

## [0.55.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.55.0) - 2026-03-10

## Breaking Changes









### Dependency Changes
* Pydantic v1 runtime support removed - Pydantic v2 is now required as a runtime dependency. Users running datamodel-code-generator with Pydantic v1 installed must upgrade to Pydantic v2. The previously deprecated v1 compatibility layer has been completely removed. (#3025)

### Default Behavior Changes
* Default output model switched from Pydantic v1 to v2 - Running `datamodel-codegen` without specifying `--output-model-type` now generates Pydantic v2 models (`pydantic_v2.BaseModel`) instead of Pydantic v1 models (`pydantic.BaseModel`). Users who depend on the previous default behavior must now explicitly specify `--output-model-type pydantic.BaseModel` to continue generating Pydantic v1 compatible code. (#3029)

### Code Generation Changes
* Generated model syntax changed for default output - The default generated code now uses Pydantic v2 syntax including `RootModel` instead of `__root__` fields, native union syntax (`str | None`) instead of `Optional[str]`, and Pydantic v2 validator/serializer decorators. Existing code that consumes generated models may need updates to work with the new Pydantic v2 output format. (#3029)
* Pydantic v1 output support removed - The `pydantic.BaseModel` output model type has been completely removed. Generated code now only supports Pydantic v2 patterns including `RootModel` instead of `__root__`, `model_rebuild()` instead of `update_forward_refs()`, and `model_config` instead of `class Config`. Users generating Pydantic v1 models must migrate to v2 output. (#3031)

### Custom Template Update Required
* Pydantic v1 templates removed - The following Jinja2 templates have been deleted and users with custom templates extending them must migrate to v2 equivalents:
  - `pydantic/BaseModel.jinja2` → use `pydantic_v2/BaseModel.jinja2`
  - `pydantic/BaseModel_root.jinja2` → use `pydantic_v2/RootModel.jinja2`
  - `pydantic/Config.jinja2` → removed (v2 uses `model_config` dict)
(#3031)

### API/CLI Changes
* `--output-model-type pydantic.BaseModel` removed - The `pydantic.BaseModel` value for `--output-model-type` is no longer valid. Use `pydantic_v2.BaseModel` instead (now the default). (#3031)
* Pydantic v1 compatibility utilities removed from Python API - The following functions were removed from `datamodel_code_generator.util`: `model_dump()`, `model_validate()`, `get_fields_set()`, `model_copy()`. Use Pydantic v2 methods directly (`obj.model_dump()`, `cls.model_validate()`, etc.). (#3031)
* `datamodel_code_generator.model.pydantic` module removed - The entire Pydantic v1 model module including `BaseModel`, `CustomRootType`, `DataModelField`, `DataTypeManager`, and `dump_resolve_reference_action` has been removed. Use `datamodel_code_generator.model.pydantic_v2` instead. (#3031)
* Pydantic v2 now required at runtime - The minimum pydantic dependency changed from `pydantic>=1.5` to `pydantic>=2,<3`. Users with pydantic v1 installed must upgrade to pydantic v2 before using datamodel-code-generator (#3027)
* Removed internal pydantic compatibility utilities - The following functions/classes were removed from `datamodel_code_generator.util`: `get_pydantic_version()`, `is_pydantic_v2()`, `model_validator()`, `field_validator()`, and `ConfigDict`. Users who imported these internal utilities directly must update their code to use pydantic's native APIs (#3027)
* Removed `datamodel_code_generator.pydantic_patch` module - The entire pydantic compatibility patching module was removed. Any code importing from this module will fail (#3027)
* Removed `packaging` dependency - The `packaging` library is no longer a dependency. Code that relied on it being transitively available should add it explicitly (#3027)

## What's Changed
* Extract shared pydantic base module for v2/dataclass/msgspec by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3022
* Remove pydantic v1 runtime compat layer by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3025
* test: make integration helpers explicit about output model by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3028
* feat: switch default output model to pydantic v2 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3029
* test: align v2 parser baselines before v1 output removal by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3032
* feat: remove pydantic v1 output support by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3031
* docs: remove pydantic v1 references by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3030
* Remove pydantic v1 runtime compat shims and update dependencies by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3027
* docs: remove final pydantic v1 traces by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3036
* Fix all-export generation for hyphenated directories by @an5t in https://github.com/koxudaxi/datamodel-code-generator/pull/3033
* Fix exact imports for reused tree-scope models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3042
* fix #3034 and #3035 by introducing `ValidatedDefault` by @keyz in https://github.com/koxudaxi/datamodel-code-generator/pull/3040
* Avoid TYPE_CHECKING imports for Ruff modular output by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3043
* Add deprecated decorators for dataclass output by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3044
* Fix for #3045 by @ashipilov in https://github.com/koxudaxi/datamodel-code-generator/pull/3046
* Refactor generate tests to use assertion helper by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3047

## New Contributors
* @an5t made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3033
* @keyz made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3040
* @ashipilov made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3046

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.54.1...0.55.0

---

## [0.54.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.54.1) - 2026-03-04

## What's Changed
* Add dismissible announce bar to docs site by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3004
* docs: update maintainer announcement to reflect open to work status by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3007
* Support isort 8 by @cjwatson in https://github.com/koxudaxi/datamodel-code-generator/pull/3011
* Fix --allow-population-by-field-name for pydantic v2 dataclass output by @butvinm in https://github.com/koxudaxi/datamodel-code-generator/pull/3013
* Support `--use-annotated` *and* `--use-non-positive-negative-number-constrained-types` by @torarvid in https://github.com/koxudaxi/datamodel-code-generator/pull/3015
* Skip default_factory wrapping for non-callable type aliases by @butvinm in https://github.com/koxudaxi/datamodel-code-generator/pull/3012
* Fix incorrect relative imports with --use-exact-imports and --collapse-root-models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3020
* feat: Add --external-ref-mapping to import from external packages instead of generating by @matssun in https://github.com/koxudaxi/datamodel-code-generator/pull/3006
* Build(deps): Bump cryptography from 46.0.3 to 46.0.5 by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3016
* Build(deps): Bump urllib3 from 2.6.2 to 2.6.3 by @dependabot[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/3017

## New Contributors
* @matssun made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/3006

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.54.0...0.54.1

---

## [0.54.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.54.0) - 2026-02-14

## Breaking Changes



### Code Generation Changes
* Enum member names from oneOf/anyOf const constructs now use `title` field when provided - Previously, when creating enums from `oneOf`/`anyOf` constructs with `const` values, the `title` field was incorrectly ignored and enum member names were generated using the pattern `{type}_{value}` (e.g., `integer_200`). Now, when a `title` is specified, it is correctly used as the enum member name (e.g., `OK` instead of `integer_200`). Users who have code depending on the previously generated enum member names will need to update their references. (#2975)
  Before:
  ```python
  class StatusCode(IntEnum):
      integer_200 = 200
      integer_404 = 404
      integer_500 = 500
  ```
  After:
  ```python
  class StatusCode(IntEnum):
      OK = 200
      Not_Found = 404
      Server_Error = 500
  ```
* Field names matching Python builtins are now automatically sanitized - When a field name matches a Python builtin type AND the field's type annotation uses that same builtin (e.g., `int: int`, `list: list[str]`, `dict: dict[str, Any]`), the field is now renamed with a trailing underscore (e.g., `int_`) and an alias is added to preserve the original JSON field name. This prevents Python syntax issues and shadowing of builtin types. Previously, such fields were generated as-is (e.g., `int: int | None = None`), which could cause code that shadows Python builtins. After this change, the same field becomes `int_: int | None = Field(None, alias='int')`. This affects fields named: `int`, `float`, `bool`, `str`, `bytes`, `list`, `dict`, `set`, `frozenset`, `tuple`, and other Python builtins when their type annotation uses the matching builtin type. (#2968)
* $ref with non-standard metadata fields no longer triggers schema merging - Previously, when a `$ref` was combined with non-standard fields like `markdownDescription`, `if`, `then`, `else`, or other extras not in the whitelist, the generator would merge schemas and potentially create duplicate models (e.g., `UserWithExtra` alongside `User`). Now, only whitelisted schema-affecting extras (currently just `const`) trigger merging. This means:
  - Fewer merged/duplicate models will be generated
  - References are preserved directly instead of being expanded
  - Field types may change from inline merged types to direct references
  Example schema:
  ```yaml
  properties:
    user:
      $ref: "#/definitions/User"
      nullable: true
      markdownDescription: "A user object"
  ```
  Before: Could generate a merged `UserWithMarkdownDescription` model
  After: Directly uses `User | None` reference (#2993)
* Enum member names no longer get underscore suffix with `--capitalise-enum-members` - Previously, enum values like `replace`, `count`, `index` would generate `REPLACE_`, `COUNT_`, `INDEX_` when using `--capitalise-enum-members`. Now they correctly generate `REPLACE`, `COUNT`, `INDEX`. The underscore suffix is only added when `--use-subclass-enum` is also used AND the lowercase name conflicts with builtin type methods. Users relying on the previous naming (e.g., referencing `MyEnum.REPLACE_` in code) will need to update to use the new names without trailing underscores. (#2999)
* Fields using `$ref` with inline keywords now include merged metadata - When a schema property uses `$ref` alongside additional keywords (e.g., `const`, `enum`, `readOnly`, constraints), the generator now correctly merges metadata (description, title, constraints, defaults, readonly/writeOnly) from the referenced schema into the field definition. Previously, this metadata was lost. For example, a field like `type: Type` may now become `type: Type = Field(..., description='Type of this object.', title='type')` when the referenced schema includes those attributes. This also affects `additionalProperties` and OpenAPI parameter schemas. (#2997)

## What's Changed
* Refactor ruff check+format to use sequential subprocess calls by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2967
* Fix title ignored when creating enums from merging `allOf`'s or `anyOf`'s objects by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2975
* Fix aliased imports not applied to base classes and non-matching fields by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2981
* Fix handling of falsy default values for enums in set-default-enum-member option by @kkinugasa in https://github.com/koxudaxi/datamodel-code-generator/pull/2977
* Fix use_union_operator with Python builtin type field names by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2968
* Support $recursiveRef/$dynamicRef in JSON Schema and OpenAPI by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2982
* Address review feedback for recursive/dynamic ref support by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2985
* Fix RecursionError in _merge_ref_with_schema for circular $ref by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2983
* Fix missing Field import with multiple aliases on required fields by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2992
* Fix patternProperties/propertyNames key constraints lost with field_constraints by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2994
* Fix type loss when $ref is used with non-standard metadata fields by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2993
* Fix missing | None for nullable enum literals in TypedDict by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2991
* Fix exact imports with module/class name collision by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2998
* Fix extra underscore on enum members like replace with --capitalise-enum-members by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2999
* Fix merged result in parse_item not passed back to parse_object_fields by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2997
* Fix codespeed python version by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/3000
* Fix incorrect relative imports with --use-exact-imports and --collapse-root-models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2996

## New Contributors
* @kkinugasa made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2977

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.53.0...0.54.0

---

## [0.53.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.53.0) - 2026-01-12

## Breaking Changes





### Custom Template Update Required
* Parser subclass signature change - The `Parser` base class now requires two generic type parameters: `Parser[ParserConfigT, SchemaFeaturesT]` instead of just `Parser[ParserConfigT]`. Custom parser subclasses must be updated to include the second type parameter. (#2929)
  ```python
  # Before
  class MyCustomParser(Parser["MyParserConfig"]): ...


  # After
  class MyCustomParser(Parser["MyParserConfig", "JsonSchemaFeatures"]): ...
  ```
* New abstract `schema_features` property required - Custom parser subclasses must now implement the `schema_features` abstract property that returns a `JsonSchemaFeatures` (or subclass) instance. (#2929)
  ```python
  from functools import cached_property
  from datamodel_code_generator.parser.schema_version import JsonSchemaFeatures
  from datamodel_code_generator.enums import JsonSchemaVersion


  class MyCustomParser(Parser["MyParserConfig", "JsonSchemaFeatures"]):
      @cached_property
      def schema_features(self) -> JsonSchemaFeatures:
          return JsonSchemaFeatures.from_version(JsonSchemaVersion.Draft202012)
  ```
* Parser `_create_default_config` refactored to use class variable - Subclasses that override `_create_default_config` should now set the `_config_class_name` class variable instead. The base implementation uses this variable to dynamically instantiate the correct config class. (#2929)
  ```python
  # Before
  @classmethod
  def _create_default_config(cls, options: MyConfigDict) -> MyParserConfig:
      # custom implementation...
  # After
  _config_class_name: ClassVar[str] = "MyParserConfig"
  # No need to override _create_default_config if using standard config creation
  ```
* Template condition for default values changed - If you use custom Jinja2 templates based on `BaseModel_root.jinja2` or `RootModel.jinja2`, the condition for including default values has changed from `field.required` to `(field.required and not field.has_default)`. Update your custom templates if you override these files. (#2960)

### Code Generation Changes
* RootModel default values now included in generated code - Previously, default values defined in JSON Schema or OpenAPI specifications for root models were not being applied to the generated Pydantic code. Now these defaults are correctly included. For example, a schema defining a root model with `default: 1` will generate `__root__: int = 1` (Pydantic v1) or `root: int = 1` (Pydantic v2) instead of just `__root__: int` or `root: int`. This may affect code that relied on the previous behavior where RootModel fields had no default values. (#2960)
* Required fields with list defaults now use `default_factory` - Previously, required fields with list-type defaults (like `__root__: list[ID] = ['abc', 'efg']`) were generated with direct list assignments. Now they correctly use `Field(default_factory=lambda: ...)` which follows Python best practices for mutable defaults. This changes the structure of generated code for root models and similar patterns with list defaults. (#2958)
  Before:
  ```python
  class Family(BaseModel):
      __root__: list[ID] = ["abc", "efg"]
  ```
  After:
  ```python
  class Family(BaseModel):
      __root__: list[ID] = Field(default_factory=lambda: [ID.parse_obj(v) for v in ["abc", "efg"]])
  ```

## What's Changed
* Separate pytest-benchmark into dedicated benchmark dependency group by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2937
* Support ClassVar for Pydantic v2 by @ubaumann in https://github.com/koxudaxi/datamodel-code-generator/pull/2920
* Add schema version detection and feature flags by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2924
* Fix MRO ordering for multiple inheritance in GraphQL and JSON Schema/OpenAPI by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2941
* Add schema_features property to parsers for version detection by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2929
* Fix $ref handling in request-response mode for readOnly/writeOnly schemas by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2942
* Ensure codecov upload runs even when coverage check fails by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2944
* Add FeatureMetadata to schema feature classes for doc generation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2945
* Add schema-docs auto-generation with pre-commit and CI by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2949
* Add comprehensive feature metadata to schema version dataclasses by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2946
* fix: move UnionMode import outside TYPE_CHECKING for Pydantic runtime… by @phil65 in https://github.com/koxudaxi/datamodel-code-generator/pull/2950
* Fix IndexError when using --reuse-scope=tree with single file output by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2954
* Add --use-closed-typed-dict option to control PEP 728 TypedDict generation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2956
* Fix RootModel default value not being applied by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2960
* Fix required list fields ignoring empty default values by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2958
* Add GenerateConfig lazy import from top-level module by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2961
* Fix allOf array property merging to preserve child $ref by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2962
* Fix array RootModel default value handling in parser by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2963
* Fix bug in handling of graphql empty list defaults by @rpmcginty in https://github.com/koxudaxi/datamodel-code-generator/pull/2948

## New Contributors
* @ubaumann made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2920
* @phil65 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2950

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.52.2...0.53.0

---

## [0.52.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.52.2) - 2026-01-05

## What's Changed
* Add support for multiple base classes in base_class_map and customBasePath by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2916
* Add __hash__ to Pydantic v2 models used in sets by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2918
* fix: Handle class name prefix correctly in GraphQL parser by @siminn-arnorgj in https://github.com/koxudaxi/datamodel-code-generator/pull/2926
* Add TypedDict closed and extra_items support (PEP 728) by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2922
* Fix release-draft workflow to use pull_request_target and increase max_turns to 50 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2930
* Migrate from pyright to ty type checker by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2928
* Fix URL port handling in get_url_path_parts by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2933


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.52.1...0.52.2

---

## [0.52.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.52.1) - 2026-01-03

## What's Changed
* Add --validators option for Pydantic v2 field validators by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2906
* Add dynamic model generation support by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2901
* Sync zensical.toml nav with docs directory by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2908
* Add deprecation warning for default output-model-type by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2910
* Add deprecation warning and explicit --output-model-type to docs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2911
* Add llms.txt generator for LLM-friendly documentation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2912
* Move coverage fail_under check to combined coverage environment by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2909
* Fix YAML scientific notation parsing as float by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2913
* Add deprecated field support for Pydantic v2 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2915
* Add deprecation warning for Pydantic v2 without --use-annotated by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2914


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.52.0...0.52.1

---

## [0.52.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.52.0) - 2026-01-02

## Breaking Changes




### Code Generation Changes
* Union fields with titles now wrapped in named models when `--use-title-as-name` is enabled - Previously, union-typed fields with a `title` were generated as inline union types (e.g., `TypeA | TypeB | TypeC | None`). Now they generate a separate wrapper model using the title name, and the field references this wrapper type (e.g., `ProcessingStatusUnionTitle | None`). This affects code that directly accesses union field values, as they now need to access the `.root` attribute (Pydantic v2) or `.__root__` (Pydantic v1) of the wrapper model. (#2889)
  **Before:**
  ```python
  class ProcessingTaskTitle(BaseModel):
      processing_status_union: ProcessingStatusDetail | ExtendedProcessingTask | ProcessingStatusTitle | None = Field(
          "COMPLETED", title="Processing Status Union Title"
      )
  ```
  **After:**
  ```python
  class ProcessingStatusUnionTitle(BaseModel):
      __root__: ProcessingStatusDetail | ExtendedProcessingTask | ProcessingStatusTitle = Field(
          ..., title="Processing Status Union Title"
      )


  class ProcessingTaskTitle(BaseModel):
      processing_status_union: ProcessingStatusUnionTitle | None = Field(
          default_factory=lambda: ProcessingStatusUnionTitle.parse_obj("COMPLETED"),
          title="Processing Status Union Title",
      )
  ```
* Inline types with titles now generate named type aliases when `--use-title-as-name` is enabled - Arrays, dicts, enums-as-literals, and oneOf/anyOf unions that have a `title` in the schema now generate named type aliases or RootModel classes instead of being inlined. This improves readability but changes the generated type structure. For TypedDict output, generates `type MyArrayName = list[str]`. For Pydantic output, generates `class MyArrayName(RootModel[list[str]])`. (#2889)
* Default value handling changed for wrapped union fields - Fields that previously had simple default values now use `default_factory` with a lambda that calls `parse_obj()` (Pydantic v1) or `model_validate()` (Pydantic v2) to construct the wrapper model. Code that introspects field defaults will see a factory function instead of a direct value. (#2889)
* Different output for `$ref` with `nullable: true` - When a JSON Schema property has a `$ref` combined with only `nullable: true` (and optionally metadata like `title`/`description`), the generator now uses the referenced type directly with `Optional` annotation instead of creating a new merged model. For example, a schema with multiple properties referencing `User` with `nullable: true` will now generate `user_a: User | None` instead of creating separate `UserA`, `UserB` model classes. This is a bug fix that reduces redundant model generation, but existing code that depends on the previously generated class names will break. (#2890)
  Before:
  ```python
  class UserA(BaseModel):
      name: str


  class UserB(BaseModel):
      name: str


  class Model(BaseModel):
      user_a: UserA | None = None
      user_b: UserB | None = None
  ```
  After:
  ```python
  class User(BaseModel):
      name: str


  class Model(BaseModel):
      user_a: User | None = None
      user_b: User | None = None
  ```
* Type alias generation expanded for `--use-title-as-name` - When using `--use-title-as-name`, the generator now creates type aliases for additional cases: nested array items with titles, additionalProperties values with titles, oneOf/anyOf branches with titles, patternProperties, propertyNames, and primitive types with titles. Previously these were inlined; now they generate named type aliases. This is a bug fix per #2887, but changes generated output for schemas with titles on nested elements. (#2891)
* Title no longer inherited in combined schemas - In anyOf/oneOf/allOf schemas, the parent schema's `title` is now excluded when merging with child schemas. This prevents unintended title inheritance that could affect model naming when `--use-title-as-name` is enabled. (#2891)
* `allOf` with single `$ref` no longer creates wrapper class - When a schema property uses `allOf` with only a single `$ref` and no additional properties, the generator now directly references the target type instead of creating an unnecessary wrapper class. This may affect code that depends on the previously generated wrapper class names or structure. For example, a property defined as `allOf: [$ref: '#/components/schemas/ACHClass']` will now generate `ach_class: ACHClass | None` instead of creating an intermediate wrapper type. (#2902)

## What's Changed
* Add ULID and Email format documentation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2886
* Add --class-name-prefix, --class-name-suffix, and --class-name-affix-scope options by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2885
* Use class-name-suffix for parser config TypedDicts by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2888
* Create type aliases for inline types with title when use-title-as-name is enabled by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2889
* Fix duplicate model generation for $ref with nullable by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2890
* Create type aliases for nested elements with titles when use-title-as-name is enabled by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2891
* Clarify --aliases help text to explain schema field becomes Pydantic alias by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2892
* Document external library import use case for --type-overrides by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2893
* Add documentation for reducing duplicate field types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2896
* Add FutureWarning for upcoming ruff default formatters by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2895
* Add --openapi-include-paths option for path-based model filtering by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2894
* Add --graphql-no-typename option to exclude typename field by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2899
* Add --default-values CLI option for overriding field defaults by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2897
* Fix allOf with single ref creating unnecessary wrapper class by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2902
* Fix --reuse-model --collapse-reuse-models to deduplicate identical inline definitions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2903
* Add --use-serialization-alias option for Pydantic v2 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2905
* Fix Pydantic v2 discriminated unions in array fields by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2907


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.51.0...0.52.0

---

## [0.51.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.51.0) - 2026-01-01

## Breaking Changes



### Code Generation Changes
* Different output when using `--input-model` with Set, FrozenSet, Mapping, or Sequence types - When using `--input-model` to convert Pydantic models or dataclasses, types that were previously converted to `list` or `dict` are now preserved as their original Python types. For example, a field typed as `Set[str]` now generates `set[str]` instead of `list[str]`, `FrozenSet[T]` generates `frozenset[T]`, `Mapping[K, V]` generates `Mapping[K, V]` instead of `dict[K, V]`, and `Sequence[T]` generates `Sequence[T]` instead of `list[T]`. This may cause type checking differences or runtime behavior changes if your code depended on the previous output types. (#2837)
* allOf multi-ref with property overrides now preserves inheritance - Schemas using `allOf` with multiple `$ref` items where the child schema also defines properties that override parent properties will now generate classes with multiple inheritance (e.g., `class Person(Thing, Location)`) instead of a flattened single class with all properties merged inline. Previously, child property overrides were incorrectly treated as conflicts, triggering schema merging. Users relying on the flattened output may need to adjust their code. (#2838)
  Before:
  ```python
  class Person(BaseModel):
      type: str | None = "playground:Person"
      name: constr(min_length=1) | None = None
      address: constr(min_length=5)
      age: int | None = None
  ```
  After:
  ```python
  class Thing(BaseModel):
      type: str
      name: constr(min_length=1)


  class Location(BaseModel):
      address: constr(min_length=5)


  class Person(Thing, Location):
      type: str | None = "playground:Person"
      name: constr(min_length=1) | None = None
      age: int | None = None
  ```
* Ruff unsafe fixes now applied automatically - When using the `ruff-check` formatter, the `--unsafe-fixes` flag is now passed to ruff, which enables fixes that may change code behavior in potentially incorrect ways. This includes removing unused imports that might have side effects, removing unused variables that could affect debugging, and other transformations ruff considers "unsafe". Users who relied on the previous conservative safe-only fix behavior may see different generated code output. To restore the previous behavior, users can configure ruff via `pyproject.toml` or `ruff.toml` to disable specific unsafe rules. (#2847)
* Type aliases now generate as class inheritance - When using `--reuse-model` (Pydantic v2 only), models that would previously generate as type aliases (`ChildModel = ParentModel`) now generate as explicit subclasses (`class ChildModel(ParentModel): pass`). This change improves type checker compatibility and maintains proper type identity, but may affect code that relied on type alias semantics or compared types directly. (#2853)
  Before:
  ```python
  ArmLeft = ArmRight
  ```
  After:
  ```python
  class ArmLeft(ArmRight):
      pass
  ```
* Fields with `const` values in anyOf/oneOf now generate `Literal` types instead of inferred base types - Previously, a `const` value like `"MODE_2D"` in an anyOf/oneOf schema would generate `str` type. Now it generates `Literal["MODE_2D"]`. This change affects type hints in generated models and may require updates to code that type-checks against the generated output. For example:
  ```python
  # Before (v0.x)
  map_view_mode: str = Field("MODE_2D", alias="mapViewMode", const=True)
  apiVersion: str = Field("v1", const=True)
  # After (this PR)
  map_view_mode: Literal["MODE_2D"] = Field("MODE_2D", alias="mapViewMode", const=True)
  apiVersion: Literal["v1"] = Field("v1", const=True)
  ```
  This is a bug fix that makes the generated code more type-safe, but downstream code performing type comparisons or using `isinstance(field, str)` checks may need adjustment. (#2864)

### Custom Template Update Required
* New DataType flags available for custom templates - Three new boolean flags have been added to the `DataType` class: `is_frozen_set`, `is_mapping`, and `is_sequence`. Custom Jinja2 templates that inspect DataType flags may need to be updated to handle these new type variations if they contain logic that depends on exhaustive type flag checks. (#2837)
* Pydantic v2 BaseModel.jinja2 template structure changed - If you have a custom template that extends or modifies the default `pydantic_v2/BaseModel.jinja2` template, you need to update it. The conditional block that generated type aliases (`{% if base_class != "BaseModel" and ... %}{{ class_name }} = {{ base_class }}{% else %}...{% endif %}`) has been removed. Templates should now always generate class declarations. (#2853)

### Default Behavior Changes
* `--input-model-ref-strategy reuse-foreign` behavior changed - Previously, this strategy compared the source type family against the **input** model's family (e.g., if input was Pydantic, any non-Pydantic type like dataclass was considered "foreign" and reused). Now it compares against the **output** model's family. This means types that were previously imported/reused may now be regenerated, and vice versa. For example, when converting a Pydantic model containing a dataclass to TypedDict output, the dataclass was previously imported (it was "foreign" to Pydantic input), but now it will be regenerated (it's not the same family as TypedDict output). Enums are always reused regardless of output type. (#2854)

### API/CLI Changes
* Mixing config and keyword arguments now raises ValueError - Previously, `generate()` allowed passing both a `config` object and individual keyword arguments, with keyword arguments overriding config values. Now, providing both raises `ValueError: "Cannot specify both 'config' and keyword arguments. Use one or the other."` Users must choose one approach: either pass a `GenerateConfig` object or use keyword arguments, but not both. (#2874)
  ```python
  # Before (worked): keyword args overrode config values
  generate(input_=schema, config=config, output=some_path)
  # After (raises ValueError): must use one or the other
  # Option 1: Use config only (include output in config)
  config = GenerateConfig(output=some_path, ...)
  generate(input_=schema, config=config)
  # Option 2: Use keyword args only
  generate(input_=schema, output=some_path, ...)
  ```
* Parser signature simplified to config + options pattern - `Parser.__init__`, `JsonSchemaParser.__init__`, `OpenAPIParser.__init__`, and `GraphQLParser.__init__` now accept either a `config: ParserConfig` object OR keyword arguments via `**options: Unpack[ParserConfigDict]`, but not both simultaneously. Passing both raises a `ValueError`. Existing code using only keyword arguments continues to work unchanged. (#2877)
  ```python
  # Before: Could potentially mix config with kwargs (undefined behavior)
  parser = JsonSchemaParser(source="{}", config=some_config, field_constraints=True)
  # After: Raises ValueError - must use one approach or the other
  parser = JsonSchemaParser(source="{}", config=some_config)  # Use config object
  # OR
  parser = JsonSchemaParser(source="{}", field_constraints=True)  # Use keyword args
  ```
* Subclass compatibility - Code that subclasses `Parser`, `JsonSchemaParser`, `OpenAPIParser`, or `GraphQLParser` may need updates if they override `__init__` and call `super().__init__()` with explicit parameter lists. The new signature uses `**options: Unpack[ParserConfigDict]` instead of explicit parameters. (#2877)
* `Config.input_model` type changed from `str` to `list[str]` - The `input_model` field in the `Config` class now stores a list of strings instead of a single string. While backward compatibility is maintained when *setting* the value (single strings are automatically coerced to lists), code that *reads* `config.input_model` will now receive a `list[str]` instead of `str | None`. Users who programmatically access this field should update their code to handle the list type. (#2881)
  ```python
  # Before
  if config.input_model:
      process_model(config.input_model)  # config.input_model was str
  # After
  if config.input_model:
      for model in config.input_model:  # config.input_model is now list[str]
          process_model(model)
  ```

## What's Changed
* Add public API signature baselines by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2832
* Add deprecation warning for Pydantic v1 runtime by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2833
* Fix --use-generic-container-types documentation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2835
* Add extends support for profile inheritance by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2834
* Fix CLI option docstrings and add missing tests by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2836
* Preserve Python types (Set, Mapping, Sequence) in --input-model by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2837
* Replace docstring with option_description in cli_doc marker by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2839
* Fix allOf multi-ref to preserve inheritance with property overrides by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2838
* Fix x-python-type for Optional container types in anyOf schemas by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2840
* Support incompatible Python types in x-python-type extension by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2841
* Fix nested type imports in x-python-type override by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2842
* Fix deep hierarchy type inheritance in allOf property overrides by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2843
* Fix CLI doc option_description errors in tests and build script by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2846
* Add --unsafe-fixes to ruff-check formatter by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2847
* Add support for multiple aliases using Pydantic v2 AliasChoices by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2845
* Handle types.UnionType in _serialize_python_type for Python 3.10-3.13 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2848
* Fix set/frozenset duplicate output in x-python-type serialization by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2849
* Add --input-model-ref-strategy option for controlling type reuse by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2850
* Fix DataType deepcopy infinite recursion with circular references by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2852
* Add automatic handling of unserializable types in --input-model by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2851
* Fix reuse-foreign to compare with output type instead of input type by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2854
* Fix reuse-model generating type aliases instead of class inheritance by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2853
* Add AST-based type string parsing helpers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2856
* Fix x-python-type qualified name imports using AST helper by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2857
* Fix generic type import with module path by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2858
* Use __qualname__ for nested class support and add DefaultPutDict test by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2859
* fix: handle type definitions from grand(grand...) parent schemas by @simontaurus in https://github.com/koxudaxi/datamodel-code-generator/pull/2861
* Add defaultdict and Any to PYTHON_TYPE_IMPORTS by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2860
* Add defaultdict to preserved type origins for TypedDict generation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2866
* Handle Annotated types in _serialize_python_type for TypedDict generation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2867
* Remove WithJsonSchema from ExtraTemplateDataType by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2868
* Fix const in anyOf/oneOf to generate Literal type by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2864
* Optimize deepcopy for empty lists by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2862
* Fix pre-commit hooks and pytest for Windows environments by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2871
* Fix _normalize_union_str to handle nested generic types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2875
* Fix _normalize_union_str to recursively normalize nested unions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2876
* feat: add --allof-class-hierarchy option by @simontaurus in https://github.com/koxudaxi/datamodel-code-generator/pull/2869
* Simplify generate() function signature using Unpack[GenerateConfigDict] by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2874
* Simplify Parser.__init__ signature using Unpack[ParserConfigDict] by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2877
* Refactor generate() and Parser to use config directly by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2878
* Update using_as_module.md to document config parameter by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2879
* fix: Always merge multiple GraphQL schemas before parsing by @siminn-arnorgj in https://github.com/koxudaxi/datamodel-code-generator/pull/2873
* Refactor: Use model_validate/parse_obj for Parser config initialization by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2880
* Add multiple --input-model support with inheritance preservation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2881
* Exclude OpenAPI/JSON Schema extension fields (x-*) by @ahmetveburak in https://github.com/koxudaxi/datamodel-code-generator/pull/2801
* Add pre-commit hook setup instructions to contributing guide by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2882
* Consolidate ParserConfig TypedDict profiles with inheritance preservation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2883
* Add release notification workflow by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2884

## New Contributors
* @simontaurus made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2861

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.50.0...0.51.0

---

## [0.50.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.50.0) - 2025-12-28

## Breaking Changes


















### Code Generation Changes
* Models with `unevaluatedProperties` now generate extra field configuration - JSON Schemas containing `unevaluatedProperties: false` will now generate models with `extra='forbid'` (Pydantic v2) or `extra = Extra.forbid` (Pydantic v1), and schemas with `unevaluatedProperties: true` will generate `extra='allow'`. Previously this keyword was ignored. This may cause validation errors for data that was previously accepted. (#2797)
  Example - a schema like:
  ```json
  {
    "title": "Resource",
    "type": "object",
    "properties": { "name": { "type": "string" } },
    "unevaluatedProperties": false
  }
  ```
  Previously generated:
  ```python
  class Resource(BaseModel):
      name: str | None = None
  ```
  Now generates:
  ```python
  class Resource(BaseModel):
      model_config = ConfigDict(extra="forbid")
      name: str | None = None
  ```

### Default Behavior Changes
* Default encoding changed from system locale to UTF-8 - The default encoding for reading input files and writing output is now always `utf-8` instead of the system's locale-preferred encoding (e.g., `cp1252` on Windows). Users who rely on locale-specific encoding must now explicitly use `--encoding` to specify their desired encoding (#2802)

## What's Changed
* Fix missing model_config in query parameter classes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2795
* Escape backslash and triple quotes in docstrings by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2796
* Add unevaluatedProperties support by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2797
* Expose schema $id and path to template context by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2798
* Improve CLI startup time with lazy imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2799
* Use UTF-8 as default encoding instead of locale-preferred by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2802
* Add model-level json_schema_extra support for Pydantic v2 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2803
* Add input_model field support to cli_doc marker by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2805
* Add dict input support for generate() function by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2806
* Optimize extra_template_data copy in DataModel init by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2811
* Add LRU cache for file loading and path existence checks by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2810
* Optimize JSON/YAML loading with auto-detection and json.load by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2809
* Migrate docs deployment to Cloudflare Pages by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2812
* Optimize CI workflow with tox cache and remove dev check by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2815
* Fix superfluous None when using $ref with nullable type aliases by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2814
* Remove tox cache that breaks Windows CI by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2816
* Add --input-model option for Pydantic models and dicts by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2804
* Add ReadOnly support for TypedDict with --use-frozen-field by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2813
* Exclude perf tests from regular test runs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2817
* Add extreme-scale performance tests with dynamic schema generation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2818
* Add ULID type support by @ahmetveburak in https://github.com/koxudaxi/datamodel-code-generator/pull/2820
* Add --enum-field-as-literal-map option and x-enum-field-as-literal schema extension by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2821
* Fix propertyNames constraint ignored when using $ref to enum definition by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2824
* Reduce CodSpeed benchmark tests for faster CI by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2826
* Optimize propertyNames $ref handling by calling get_ref_data_type directly by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2825
* Add missing path and ulid type mappings to TypedDict type manager by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2828
* Fix --check to use output path's pyproject.toml settings by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2831

## New Contributors
* @ahmetveburak made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2820

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.49.0...0.50.0

---

## [0.49.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.49.0) - 2025-12-25

## Breaking Changes

  ### SchemaParseError for Invalid Schema Data
  * Schema validation errors now raise `SchemaParseError` instead of Pydantic `ValidationError` - When parsing invalid schema data (e.g., `"minimum": "not_a_number"`), the library now raises `SchemaParseError` instead of passing through Pydantic's `ValidationError`. Users catching `pydantic.ValidationError` for schema validation failures should update to catch `SchemaParseError`. The original error is preserved in the `original_error` attribute. (#2786)

  ## Bug Fixes

  ### CLI Now Correctly Outputs to stdout
  * Fixed CLI to actually output to stdout when `--output` is not specified - The `--output` argument has always documented `(default: stdout)` in `--help`, but previously no output was produced. Now works as documented. (#2787)

  ## Other Notable Changes

  * `generate()` function now returns `str | GeneratedModules | None` instead of `None` - Existing code ignoring the return value is unaffected. (#2787)
  * Error message for multi-module output without directory changed to be more descriptive. (#2787)

## What's Changed
* Merge duplicate breaking change headings in release notes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2776
* Optimize O(n²) algorithms and reduce redundant iterations by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2778
* Optimize performance with LRU caching and O(n) algorithms by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2777
* Optimize Jinja2 environment caching and ruff batch formatting by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2779
* Remove YAML parsing cache and deepcopy overhead by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2781
* Add performance e2e tests with large schema fixtures by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2782
* Convert Import class from Pydantic to dataclass for performance by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2783
* Add schema path context to error messages by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2786
* Return str or dict when output=None in generate() by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2787
* Add --http-timeout CLI option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2788
* Pass schema extensions to templates by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2790
* Add propertyNames and x-propertyNames support by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2789
* Add support for additional_imports in extra-template-data JSON by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2793
* Update zensical to 0.0.15 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2794
* Add --use-field-description-example option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2792
* Add --collapse-root-models-name-strategy option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2791


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.48.0...0.49.0

---

## [0.48.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.48.0) - 2025-12-24

## Breaking Changes
















### Code Generation Changes
* Custom class name generator now applied consistently during duplicate name resolution - Previously, when using `custom_class_name_generator`, the default PascalCase naming was incorrectly applied during duplicate name resolution. Now the custom generator is respected throughout, which may change generated class names. For example, a class name like `nested_object_result` with a custom generator `f"Custom{name}"` will now produce `CustomNested_object_result` instead of `CustomNestedObjectResult`. Users relying on the previous behavior should update their code to expect the new, correct class names. (#2757)

* YAML 1.1 boolean keywords now preserved as strings in enums - Values like `YES`, `NO`, `on`, `off`, `y`, `n` that were previously converted to Python booleans are now preserved as their original string values. This fixes issues where string enum values were incorrectly converted but may change generated output for schemas that relied on the previous behavior. For example, a YAML enum with `YES` will now generate `YES = 'YES'` instead of being converted to `True`. (#2767)

### Default Behavior Changes
* YAML boolean parsing restricted to YAML 1.2 semantics - Only `true`, `false`, `True`, `False`, `TRUE`, `FALSE` are now recognized as boolean values. YAML 1.1 boolean aliases (`yes`, `no`, `on`, `off`, `y`, `n`, etc.) are no longer parsed as booleans and will be treated as strings. Non-lowercase forms (`True`, `False`, `TRUE`, `FALSE`) now emit a deprecation warning indicating future versions will only support lowercase `true`/`false`. (#2767)

## What's Changed
* Fix Google Analytics config for Zensical by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2748
* ci: add release draft workflow with Claude Code Action by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2749
* fix: improve release-draft workflow configuration by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2750
* fix: quote JSON schema in claude_args to preserve double quotes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2751
* fix: remove redundant --output-format json from claude_args by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2752
* fix: increase max-turns from 5 to 10 for structured output by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2753
* chore: increase max-turns to 20 for better margin by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2754
* Add pydantic_v2.dataclass output type and remove pydantic v1 dataclass by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2746
* Fix Pydantic v2 deprecation warnings by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2747
* Add --use-tuple-for-fixed-items option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2756
* Fix custom_class_name_generator not applied consistently by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2757
* Add --base-class-map option for model-specific base classes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2759
* Support 'timestamp with time zone' format by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2762
* Add --type-overrides option to replace schema types with custom Python types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2758
* Add --use-root-model-type-alias option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2763
* Add --class-decorators option for custom model decorators by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2760
* Add --naming-strategy and --duplicate-name-suffix CLI options by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2761
* Add --generate-prompt option for LLM consultation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2764
* Add pydantic_v2.dataclass to output model types documentation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2765
* Clarify --input-file-type help text and CLI documentation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2768
* Support boolean values in patternProperties for JSON Schema 2020-12 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2766
* Use YAML 1.2-like bool semantics to fix YES/NO/on/off enum issues by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2767
* Add InvalidFileFormatError with detailed error messages by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2771
* Merge multiple patternProperties with same value type into single regex pattern by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2770
* Sync Common Recipes and badges between README and docs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2773
* Fix primary-first naming for multi-file schemas with same-named definitions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2772
* Optimize performance for large schema processing by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2774


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.47.0...0.48.0

---

## [0.47.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.47.0) - 2025-12-23

## Breaking Changes

### Code Generation Changes
* RootModel defaults use direct instantiation - RootModel fields with default values now generate `ClassName(value)` instead of `ClassName.model_validate(value)`. This produces cleaner code but changes the generated output (#2714)

* `--strict-nullable` now applies to JSON Schema - The `--strict-nullable` option is no longer OpenAPI-only and has been moved to Field customization options. It now also correctly respects `nullable` on array items (#2713, #2727)

### Custom Template Update Required
* If you use custom Jinja2 templates that check `field.nullable`, you may need to update them. The `nullable` field on JsonSchemaObject now defaults to `None` instead of `False`. Templates should check `field.nullable is True` instead of just `if field.nullable` (#2715)

  Example change:
  ```jinja2
  {# Before #}
  {%- if field.nullable %}...{% endif %}

  {# After #}
  {%- if field.nullable is true %}...{% endif %}
  ```

### Error Handling Changes
* Formatting failures emit warning instead of error - When code formatting fails (e.g., due to black errors), the generator now emits an unformatted output with a warning instead of raising an exception. This allows code generation to succeed even when formatting tools encounter issues (#2737)

## What's Changed
* Require @cli_doc marker for all CLI options by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2712
* fix: respect nullable on array items with --strict-nullable by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2713
* fix: wrap RootModel primitive defaults with default_factory by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2714
* Add --use-default-factory-for-optional-nested-models option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2711
* Fix nullable field access in custom templates with strict_nullable by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2715
* fix: skip non-model types in __change_field_name by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2717
* Add requestBodies scope support for OpenAPI by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2716
* Fix test data backspace escape by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2718
* fix: quote forward references in recursive RootModel generic parameters by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2720
* Add force_exec_validation option to catch runtime errors across Python versions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2719
* Add validation for extra_args in test helper functions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2723
* Fix discriminator with allOf without Literal type for Pydantic v2 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2722
* Fix regex_engine config not applied to RootModel generic by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2721
* Fix hostname format with field_constraints to use Field(pattern=...) by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2724
* Move --strict-nullable from OpenAPI-only to Field customization by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2727
* Run CLI doc coverage test in CI without --collect-cli-docs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2728
* Add --use-generic-base-class option for DRY model config by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2726
* Refactor parser base post-processing for DRY and type-safe implementation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2730
* Add --collapse-reuse-models option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2731
* Add --field-type-collision-strategy option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2733
* Revert "Add --field-type-collision-strategy option" by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2734
* Add --no-treat-dot-as-module option for flat output structure by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2732
* Add --field-type-collision-strategy option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2735
* Add --use-standard-primitive-types option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2736
* Emit unformatted output when formatting fails by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2737
* Fix aliasing of builtin type field names by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2738
* Add path filters to optimize CodeRabbit reviews by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2742
* Add --output-date-class option and date-time-local format support by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2739
* Fix custom template directory not working for included templates by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2740
* Remove unnecessary model_config from RootModel subclasses by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2741
* Fix incorrect --type-mappings examples in documentation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2744
* Add Python 3.13 deprecation warning documentation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2743
* Add admonition support to CLI docs and document --use-default nullable behavior by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2745


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.46.0...0.47.0

---

## [0.46.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.46.0) - 2025-12-20

## Breaking Changes

 ### Python Version
 * Python 3.9 support dropped - Minimum Python version is now 3.10+ (#2692)

 ### Default Behavior Changes
 * Native type hints by default - `list[...]`/`dict[...]` are now used instead of `List[...]`/`Dict[...]`. Use `--no-use-standard-collections` to restore previous behavior (#2699)
 * Union operator by default - `X | Y` syntax is now used instead of `Union[X, Y]`/`Optional[X]`. Use `--no-use-union-operator` to restore previous behavior (#2703)

 ### Code Generation Changes
 * Nullable required fields no longer have default values (Pydantic v2) - Fields marked as both `required` and `nullable` no longer get `= None`. The `pydantic_v2/BaseModel.jinja2` template logic was updated to only assign default values when `field.required` is false. This fixes incorrect behavior where required fields could be omitted (#2520)
 * TypedDict respects enum_field_as_literal setting - TypedDict output now respects user's `--enum-field-as-literal` setting instead of forcing `all`. Added new `--enum-field-as-literal none` option (#2691)
 * prefixItems now generates tuple types - JSON Schema `prefixItems` with matching `minItems`/`maxItems` and no `items` now generates `tuple[T1, T2, ...]` instead of `list[...]`. This applies when the array has a fixed length with heterogeneous types (#2537)


## What's Changed
* feat: Add --use-status-code-in-response-name option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2688
* feat: Add support for number type with time-delta format by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2689
* docs: Add CHANGELOG.md with auto-update workflow by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2690
* feat: Add --enum-field-as-literal none option and respect user settings for TypedDict by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2691
* docs: Clarify default encoding behavior in documentation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2693
* ci: Improve CLI docs generation workflow by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2695
* feat(docs): Group CLI examples by schema type with tabs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2696
* feat: Add --ignore-enum-constraints option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2694
* Add zensical.toml configuration by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2697
* fix: Make docs generation deterministic by sorting glob results and dict iterations by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2698
* feat: Drop Python 3.9 support, require Python 3.10+  by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2692
* fix: Update zensical dependency version to 0.0.13 for Python 3.10+ by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2700
* feat: Default to native list/dict type hints instead of typing.List/Dict by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2699
* fix: Remove unnecessary pass statement when nested class exists by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2704
* build: Replace pre-commit with prek by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2702
* fix: Improve state management in Imports, DataType, and DataModel classes by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2705
* feat: Default to union operator for type hints by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2703
* fix: handle fork PRs in lint workflow by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2706
* fix: handle fork PRs in readme and cli-docs workflows by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2707
* Nullable required fields should not have default value by @raymondbutcher in https://github.com/koxudaxi/datamodel-code-generator/pull/2520
* Add support for prefixItems to emit tuples by @saulshanabrook in https://github.com/koxudaxi/datamodel-code-generator/pull/2537
* Remove unnecessary flags from --check test by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2708
* fix: update expected files to use modern union type syntax (`str | None`) by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2709
* fix: trigger docs build after changelog update by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2710

## New Contributors
* @raymondbutcher made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2520
* @saulshanabrook made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2537

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.45.0...0.46.0

---


## [0.45.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.45.0) - 2025-12-19

## What's Changed
* docs: add cross-links between CLI reference and usage documentation pages by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2678
* Migrate documentation build from MkDocs to Zensical by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2679
* docs: Improve landing page, README, and add FAQ with GitHub issue links by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2680
* docs: Comprehensive documentation improvements with CLI reference enhancements by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2681
* feat: Add documentation and GitHub links to --help output by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2682
* feat: Add official GitHub Action for CI/CD integration by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2683
* feat: Add watch mode for automatic code regeneration with debounce delay by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2686
* feat: Add --enable-command-header option to include command-line in file headers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2684
* feat: Add --module-split-mode option to generate one file per model (#1170) by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2685


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.44.0...0.45.0

---

## [0.44.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.44.0) - 2025-12-18

## What's Changed
* Fix empty dict/list defaults not generating default_factory for Pydantic models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2655
* Add --use-decimal-for-multiple-of option to avoid floating-point precision issues by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2656
* Remove mock remote ref test by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2659
* Skip `from __future__ import annotations` for Python 3.14+ targets (PEP 649) by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2658
* Fix enum member names conflicting with builtin type methods by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2660
* Fix field name shadowing check to use issubclass for Pydantic v2 derived types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2657
* Fix keep_model_order dependency ordering and reduce unnecessary model_rebuild() calls by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2661
* Fix missing __init__.py in intermediate package directories by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2662
* Fix msgspec tag_field conflict with discriminator field definition by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2663
* Fix dataclass field ordering conflict when inheriting from parent with default fields by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2664
* Fix msgspec mutable default values to use default_factory by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2666
* Add compile/exec validation for generated Python code in tests by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2665
* Fix allOf array items partial override to inherit parent item types instead of Any by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2667
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/2668
* Fix --use-unique-items-as-set to output set literals for default values by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2672
* Fix allOf partial override to inherit parent constraints and add --allof-merge-mode option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2671
* Fix GraphQL parser to handle renamed objects correctly by @siminn-arnorgj in https://github.com/koxudaxi/datamodel-code-generator/pull/2670
* Add auto-generated CLI reference documentation from test cases by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2673
* Fix crash when parsing enum containing only null value by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2674
* Fix allOf with $ref to root model losing constraints by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2676
* Support enum-field-as-literal in GraphQL parser by @siminn-arnorgj in https://github.com/koxudaxi/datamodel-code-generator/pull/2677
* refactor: reduce e2e test duplication with parameterization and enhance CLI docs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2675


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.43.1...0.44.0

---

## [0.43.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.43.1) - 2025-12-12

## What's Changed
* Add support for x-enumNames extension by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2653
* Fix allOf partial property overrides to inherit parent types instead of Any by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2654


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.43.0...0.43.1

---

## [0.43.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.43.0) - 2025-12-10

## What's Changed
* Fix extra blank line after custom file header with future imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2638
* Fix GraphQL enum renaming issue by @siminn-arnorgj in https://github.com/koxudaxi/datamodel-code-generator/pull/2642
* Fix allOf with description generating alias instead of class (Pydantic v2) by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2643
* Fix enum default values using full path instead of short name in same module by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2644
* Fix --url option to resolve local fragment refs correctly by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2646
* Fix allOf with single $ref to preserve class inheritance by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2647
* Fix incorrect import when module and class have the same name by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2648
* Fix nullable propagation from $ref schema targets by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2649
* Add --use-frozen-field option for JSON Schema readOnly support by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2650
* Add validation error for --use-specialized-enum when target Python version < 3.11 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2651
* Fix discriminator to use enum value instead of model name for single-value enums by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2652

## New Contributors
* @siminn-arnorgj made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2642

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.42.2...0.43.0

---

## [0.42.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.42.2) - 2025-12-08

## What's Changed
* Fix Python 3.9 compatibility regression in _scc.py by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2628
* Fix Python 3.9 compatibility: CI environment names by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2630
* Fix msgspec type hint generation for oneOf/anyOf with null type by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2629
* Fix missing imports for nested references when using --collapse-root-models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2631
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/2632
* Fix unnecessary underscore suffix for field names in non-Pydantic models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2633
* Fix enum generation from oneOf/anyOf with const values by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2634
* Fix "A Parser can not resolve classes" error when allOf references enum from another schema by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2636
* Fix $ref not merging with additional schema keywords by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2635
* Extract helper methods and reduce code duplication across parsers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2637


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.42.1...0.42.2

---

## [0.42.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.42.1) - 2025-12-08

## What's Changed
* Add named profile support and --ignore-pyproject option for pyproject.toml configuration by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2619
* Fix _internal module self-importing due to cached path property by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2621
* Add warning when components/schemas is empty but paths exist by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2622
* Fix msgspec discriminated union ClassVar generation and nullable type handling by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2620
* Fix extras lost in oneOf/anyOf structures with --field-include-all-keys by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2623
* Fix msgspec ClassVar generation for discriminator fields without   use_annotated by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2625
* Fix --check mode ignoring pyproject.toml formatter settings by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2626
* Use SerializeAsAny for pydantic v2 models with subclasses by @tobias-bahls in https://github.com/koxudaxi/datamodel-code-generator/pull/2612

## New Contributors
* @tobias-bahls made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2612

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.42.0...0.42.1

---

## [0.42.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.42.0) - 2025-12-08

## What's Changed
* Improve type resolution for inherited fields in allOf with complex schemas by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2614
* Fix circular imports in generated multi-module packages by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2613
* Wrap RootModel default values with type constructors by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2615
* Strict types now enforce field constraints by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2617
* Use default_factory for $ref with default values when using --use-annotated by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2618

## Breaking Changes

### Custom Template Update Required

If you use custom Jinja2 templates, you may need to update them to handle `default_factory` correctly with `--use-annotated`.

A new property `field.has_default_factory_in_field` has been added. Templates should check this property to avoid generating duplicate
default assignments when `Field(default_factory=...)` is used.

Example change in templates:
```jinja2
{# Before #}
{%- if not (field.required or ...) %} = {{ field.represented_default }}{% endif %}

{# After #}
{%- if not field.has_default_factory_in_field and not (field.required or ...) %} = {{ field.represented_default }}{% endif %}
```

Affected built-in templates:
- pydantic/BaseModel.jinja2
- pydantic/BaseModel_root.jinja2
- pydantic_v2/BaseModel.jinja2
- pydantic_v2/RootModel.jinja2


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.41.0...0.42.0

---

## [0.41.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.41.0) - 2025-12-05

## What's Changed
* Fix:  resolve relative $ref correctly when fetching schemas from HTTP URLs by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2600
* Add --read-only-write-only-model-type option for OpenAPI readOnly/writeOnly support by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2587
* Add file:// URL protocol support for $ref resolution by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2601
* Fix: Improve reference resolution and handling of duplicate names by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2602
* Prevent invalid relative imports in single-file output for allOf/anyOf schemas by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2604
* Fix single-file treat-dot-as-module by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2603
* allOf ignores $ref when referencing oneOf/anyOf schemas by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2605
* Resolve relative $ref correctly in external path/webhook files by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2606
* Resolve $ref to locally defined $id without network access by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2608
* Correct import generation for dot notation schema names with inheritance by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2607
* Fix import alias if two types are provided (Json schema) by @Killian-fal in https://github.com/koxudaxi/datamodel-code-generator/pull/2611
* Use Enum Members in Discriminators with Safe Imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2609

## New Contributors
* @Killian-fal made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2611

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.40.0...0.41.0

---

## [0.40.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.40.0) - 2025-12-03

## What's Changed
* Fix invalid Union syntax when using --collapse-root-models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2594
* Fix enum member names for object values to use title/name/const keys by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2596
* Fix import handling in --collapse-root-models to exclude both Optional and Union by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2597
* Fix default_factory for Union types with dict defaults  by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2595
* Fix RootModel generation order to define referenced types first by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2592
* Fix TypeAlias with circular reference to class generates NameError by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2591
* Fix __future__ import placement with custom file headers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2589
* Fix multiple types in array not generating Union when object has   properties by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2590
* Add scoped alias support for class-specific field renaming by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2599
* Fix: Handle dots in title when using --use-title-as-name option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2598


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.39.0...0.40.0

---

## [0.39.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.39.0) - 2025-12-02

## What's Changed
* Add --all-exports-scope and --all-exports-collision-strategy options for recursive exports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2588


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.38.0...0.39.0

---

## [0.38.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.38.0) - 2025-12-02

## What's Changed
* Fix transitive local reference resolution in external schema files by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2577
* Fix crash when parsing JSON Schema with internal references from stdin by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2578
* Fix allOf with multiple refs having same property name generates broken models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2579
* Fix external $ref resolution in paths when components/schemas is missing by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2581
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci[bot] in https://github.com/koxudaxi/datamodel-code-generator/pull/2582
* Fix incorrect relative import path for namespaced schemas referencing subnamespaces by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2580
* Add --generate-cli-command option to generate CLI command from pyproject.toml by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2583
* Add --check option for CI verification of generated files by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2585
* Add --use-all-exports option to generate `__all__` in `__init__.py` by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2586
* Fix empty array items to generate List[Any] instead of bare List by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2584


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.37.0...0.38.0

---

## [0.37.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.37.0) - 2025-12-01

## What's Changed
* Add PEP 257 docstrings to modules and test files by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2548
* Strict type annotations for DataType, Reference, and JsonSchemaParser by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2549
* ci: enable pytest-xdist parallel execution (-n auto) by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2553
* Add `--generate-pyproject-config` CLI option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2551
* Refactor: Consolidate e2e test helpers and eliminate code duplication by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2552
* Perf: Lazy import inflect module to reduce import time by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2554
* Use time-machine for faster freeze_time by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2555
* Lazy import black and isort for faster test startup by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2556
* Fix: Add pragma no cover for legacy version compatibility code in format.py by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2557
* Improve test coverage by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2558
* Fix --keep-model-order to respect TypeAliasType field dependencies by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2560
* Add --type-mappings option to customize type mappings in JSON schema by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2559
* Restrict freezegun dependency to Python < 3.10 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2561
* Update .gitignore to include coverage files and IDE configurations by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2563
* Remove JetBrains from sponsors section by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2564
* fix: set not nullable not required Struct fields to UnsetType by @CharString in https://github.com/koxudaxi/datamodel-code-generator/pull/2504
* Add support for `webhooks` in `--openapi-scopes` by @HNygard in https://github.com/koxudaxi/datamodel-code-generator/pull/2481
* add dataclass arguments by @ICEPower420 in https://github.com/koxudaxi/datamodel-code-generator/pull/2437
* Preserve Aliased Imports During Cleanup by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2569
* Bugfix-TypeAlias-Rebuild ---> Main by @raj-open in https://github.com/koxudaxi/datamodel-code-generator/pull/2566
* Use TypeAlias instead of TypeAliasType for non-Pydantic v2 models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2562
* Support allOf polymorphism with discriminator in OpenAPI by @zdenecek in https://github.com/koxudaxi/datamodel-code-generator/pull/2530
* Improve reference validation and reserved name handling by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2568
* Fix: enable inline-snapshot update by using assert instead of pytest.fail by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2571
* Add use_attribute_docstrings Support by @voteblake in https://github.com/koxudaxi/datamodel-code-generator/pull/2550
* Fix recursive TypeAlias generation for Python 3.11 (covers mutual alias refs) by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2570
* Add automatic README command help update via GitHub Actions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2574
* Fix README auto-update workflow to trigger CI with PAT by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2575
* Add --skip-root-model option to skip generating root model by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2572
* Add --reuse-scope option for cross-file model deduplication by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2573

## New Contributors
* @CharString made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2504
* @HNygard made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2481
* @ICEPower420 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2437
* @raj-open made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2566
* @zdenecek made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2530
* @voteblake made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2550

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.36.0...0.37.0

---

## [0.36.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.36.0) - 2025-11-26

## Breaking Changes
* OpenAPI `byte`-formatted string properties for Pydantic v2 are now generated as `bytes` fields that Pydantic automatically decodes from base64-encoded strings at runtime. Code that relied on these fields being plain `str` values (the encoded representation) may need to be updated. by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2511 (closes https://github.com/koxudaxi/datamodel-code-generator/issues/189)

* Enums inferred from OpenAPI / JSON Schema string or integer enums are now generated as specialized `StrEnum` / `IntEnum` subclasses by default when supported by the target Python version. This changes the base class of existing generated enums and can affect comparisons, JSON encoding, and downstream type checks. You can opt out using the `--no-use-specialized-enum` CLI flag or `use_specialized_enum = false` in the configuration file. by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2512 (closes https://github.com/koxudaxi/datamodel-code-generator/issues/1313 and https://github.com/koxudaxi/datamodel-code-generator/issues/2534)

* For some Pydantic v2 collection schemas that previously generated a `RootModel` wrapper (for example the `MyArray` case described in https://github.com/koxudaxi/datamodel-code-generator/issues/1830), the generated code now uses a `TypeAlias` instead of a dedicated model class. Projects that import or subclass such wrapper models may need to adjust to use the alias instead. by @butvinm in https://github.com/koxudaxi/datamodel-code-generator/pull/2505 (closes https://github.com/koxudaxi/datamodel-code-generator/issues/1848, https://github.com/koxudaxi/datamodel-code-generator/issues/2018, https://github.com/koxudaxi/datamodel-code-generator/issues/2427, and https://github.com/koxudaxi/datamodel-code-generator/issues/2487)


## What's Changed
* Add --use-type-alias flag to generate TypeAlias instead of root models by @butvinm in https://github.com/koxudaxi/datamodel-code-generator/pull/2505
* Fix Pydantic v1 runtime support by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2538
* Use subclass enum for GraphQL enums when the relative flag is used by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2514
* Add support for OpenAPI `byte` in Pydantic v2 by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2511
* Add support for specialized enums as StrEnum and IntEnum by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2512
* [Chore] Clean-up `TYPE_CHECKING` blocks by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2539
* Add support for unquoted `null` type by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2542
* Add inline-snapshot test dependency by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2546
* Support isort 7 by @jas4711 in https://github.com/koxudaxi/datamodel-code-generator/pull/2521
* Add inline-snapshot for test expected value management by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2544
* Add --use-inline-field-description option for inline docstring format by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2547


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.35.0...0.36.0

---

## [0.35.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.35.0) - 2025-10-09

## What's Changed
* Add support to Python 3.14 by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2469


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.34.0...0.35.0

---

## [0.34.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.34.0) - 2025-09-28

## What's Changed
* TypedDict: Add support for const fields using Literal by @erandagan in https://github.com/koxudaxi/datamodel-code-generator/pull/2475
* Correct custom CodeFormatter error message by @MrSnapperVibes in https://github.com/koxudaxi/datamodel-code-generator/pull/2489
* dataclass: Add support for const fields using Literal[T] by @ysndr in https://github.com/koxudaxi/datamodel-code-generator/pull/2486
* feat: Add argument to disable the __future__ annotations import by @daviddmd in https://github.com/koxudaxi/datamodel-code-generator/pull/2498
* Fix: Objects with additionalProperties shouldn't be unioned with None by default by @erandagan in https://github.com/koxudaxi/datamodel-code-generator/pull/2493

## New Contributors
* @erandagan made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2475
* @MrSnapperVibes made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2489
* @ysndr made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2486
* @daviddmd made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2498

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.33.0...0.34.0

---

## [0.33.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.33.0) - 2025-08-14

## What's Changed
* Use Pytest `tmp_path` fixture instead of `TemporaryDirectory` by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2463
* Revert default date-time behaviour to respect OpenAPI/JSON Schema specifications by @mueslo in https://github.com/koxudaxi/datamodel-code-generator/pull/2442
* Avoid mixing up configurations of different objects with the same name by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2461
* Fix Pydantic `@model_validator()` usage  for Pydantic 2.12 by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2472
* Run the test suite daily to check breaking changes by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2479
* Automatically infer CSV file type by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2467
* Avoid collapsing models when referencing a forwarding reference by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2465

## New Contributors
* @mueslo made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2442

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.32.0...0.33.0

---

## [0.32.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.32.0) - 2025-07-25

## What's Changed
* modify __typename field to honor `--use-default-kwarg` flag by @rpmcginty in https://github.com/koxudaxi/datamodel-code-generator/pull/2420
* Fix typo in variable name by @eltoder in https://github.com/koxudaxi/datamodel-code-generator/pull/2439
* Send use_non_positive_negative_number_constrained_types to the data type manager by @torarvid in https://github.com/koxudaxi/datamodel-code-generator/pull/2425
* Allow parsing non-string lists for required fields by @HeroGamers in https://github.com/koxudaxi/datamodel-code-generator/pull/2446
* Allow including path parameters in generated models by @MrLoh in https://github.com/koxudaxi/datamodel-code-generator/pull/2445
* Passing in treat_dot_as_module bool to Enum init function by @LukeAtThat in https://github.com/koxudaxi/datamodel-code-generator/pull/2456
* fix: correctly handle multiline comments in Unions by @anor4k in https://github.com/koxudaxi/datamodel-code-generator/pull/2454
* Return Parameter and RequestBody DataTypesare  by @MrLoh in https://github.com/koxudaxi/datamodel-code-generator/pull/2444

## New Contributors
* @rpmcginty made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2420
* @eltoder made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2439
* @torarvid made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2425
* @HeroGamers made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2446
* @MrLoh made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2445
* @LukeAtThat made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2456
* @anor4k made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2454

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.31.2...0.32.0

---

## [0.31.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.31.2) - 2025-06-22

## What's Changed
* fix: prevent code injection through filename in generated headers by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2428


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.31.1...0.31.2

---

## [0.31.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.31.1) - 2025-06-17

## What's Changed
* fix: documentation for --allow-extra-fields by @cosmo-grant in https://github.com/koxudaxi/datamodel-code-generator/pull/2416
* fix: respect `--extra-fields` option in pydantic v2 models by @cosmo-grant in https://github.com/koxudaxi/datamodel-code-generator/pull/2423


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.31.0...0.31.1

---

## [0.31.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.31.0) - 2025-06-12

## What's Changed
* Resolve capitalize-enum-member not working with use-subclass-enum and snake-case-field when typed enum by @kevin-paulson-mindbridge-ai in https://github.com/koxudaxi/datamodel-code-generator/pull/2418
* feat: add `--extra-fields` option to allow, forbid, or ignore extra fields by @cosmo-grant in https://github.com/koxudaxi/datamodel-code-generator/pull/2417
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2415


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.30.2...0.31.0

---

## [0.30.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.30.2) - 2025-06-07

## What's Changed
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2393
* Support `coerce_numbers_to_str` in `ConfigDict` by @robotadam in https://github.com/koxudaxi/datamodel-code-generator/pull/2394
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2397
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2402
* fix: Unnecessary _aliased suffix added to models generated from GraphQL union types by @kyo-ago in https://github.com/koxudaxi/datamodel-code-generator/pull/2396
* fix: regression where _deep_merge() mutates list values in passed dictionary by @cosmo-grant in https://github.com/koxudaxi/datamodel-code-generator/pull/2400
* Handle JSON pointer escaped values by @jrnold in https://github.com/koxudaxi/datamodel-code-generator/pull/2401
* fix regex rewrite bug by @minomocca in https://github.com/koxudaxi/datamodel-code-generator/pull/2404
* Add `--frozen-dataclasses` flag to generate frozen dataclasses by @K-dash in https://github.com/koxudaxi/datamodel-code-generator/pull/2408
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2410
* fix generation of discriminated union values with no mapping by @kymckay in https://github.com/koxudaxi/datamodel-code-generator/pull/2412

## New Contributors
* @robotadam made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2394
* @kyo-ago made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2396
* @cosmo-grant made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2400
* @jrnold made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2401
* @minomocca made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2404
* @K-dash made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2408
* @kymckay made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2412

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.30.1...0.30.2

---

## [0.30.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.30.1) - 2025-04-28

## What's Changed
* fix: TypeError: '<' not supported between instances of 'str' and 'NoneType' by @gjcarneiro in https://github.com/koxudaxi/datamodel-code-generator/pull/2380
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2383
* fixes link to templates by @leonlowitzki in https://github.com/koxudaxi/datamodel-code-generator/pull/2382
* capitalise-enum-members not working since v0.28.5 by @kevin-paulson-mindbridge-ai in https://github.com/koxudaxi/datamodel-code-generator/pull/2389

## New Contributors
* @leonlowitzki made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2382
* @kevin-paulson-mindbridge-ai made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2389

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.30.0...0.30.1

---

## [0.30.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.30.0) - 2025-04-17

## What's Changed
* feat: add new option --parent-scoped-naming to avoid name collisions by @gjcarneiro in https://github.com/koxudaxi/datamodel-code-generator/pull/2373


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.29.0...0.30.0

---

## [0.29.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.29.0) - 2025-04-17

## What's Changed
* Fix shadowed import errors by aliasing conflicting symbols in generated models by @sternakt in https://github.com/koxudaxi/datamodel-code-generator/pull/2379
* fix: Pass encoding parameter to parser in init.generate() by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2367
* Bump upper version limit on the inflect library from 6 to 8 by @gothicVI in https://github.com/koxudaxi/datamodel-code-generator/pull/2375
* feat: save memory in pydantic2 by avoiding simple aliasing empty classes by @gjcarneiro in https://github.com/koxudaxi/datamodel-code-generator/pull/2376
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2372
* fix: capitalize_enum_members not recognized in pyproject.toml by @LordFckHelmchen in https://github.com/koxudaxi/datamodel-code-generator/pull/2363
* fix(parser): ignore discriminator in collapsed list fields by @rcarriga in https://github.com/koxudaxi/datamodel-code-generator/pull/2378

## New Contributors
* @LordFckHelmchen made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2363
* @rcarriga made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2378

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.28.5...0.29.0

---

## [0.28.5](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.28.5) - 2025-03-24

## What's Changed
* fix: Fix Union type parsing with recursive None removal by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2342
* Ensure we have a string before trying to strip by @gothicVI in https://github.com/koxudaxi/datamodel-code-generator/pull/2354
* fix: Fix invalid imports for schema files with hyphens and dots by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2343
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2347
* fix: fix field has same name on pydantic v2 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2355
* fix: fix referenced default by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2356
* Fix field aliasing on discriminator fields by @djb7 in https://github.com/koxudaxi/datamodel-code-generator/pull/2349
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2360

## New Contributors
* @gothicVI made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2354
* @djb7 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2349

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.28.4...0.28.5

---

## [0.28.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.28.4) - 2025-03-11

## What's Changed
* feat: add ruff formatters by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2340


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.28.3...0.28.4

---

## [0.28.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.28.3) - 2025-03-10

## What's Changed
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2331
* [Chore] Remove Python 3.8 from tox by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2335
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2339
* Fix #2333. Remove not none arg parse defaults. by @ilovelinux in https://github.com/koxudaxi/datamodel-code-generator/pull/2334

## New Contributors
* @ilovelinux made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2335

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.28.2...0.28.3

---

## [0.28.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.28.2) - 2025-02-27

## What's Changed
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2328
* Fix import and field name collisions by @butvinm in https://github.com/koxudaxi/datamodel-code-generator/pull/2327

## New Contributors
* @butvinm made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2327

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.28.1...0.28.2

---

## [0.28.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.28.1) - 2025-02-15

## What's Changed
* Graphql --set-default-enum-member by @andrew-womeldorf in https://github.com/koxudaxi/datamodel-code-generator/pull/2323

## New Contributors
* @andrew-womeldorf made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2323

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.28.0...0.28.1

---

## [0.28.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.28.0) - 2025-02-14

## What's Changed
* Drop <3.9 support as run and generation target by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2324


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.27.3...0.28.0

---

## [0.27.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.27.3) - 2025-02-11

## What's Changed
* Reuse extras instead of dependency groups by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2307
* Set line length to 120 charachters by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2310
* Use src layout by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2311
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2316
* YML to YAML by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2317
* Add more ruff checks and use defaults by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2318


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.27.2...0.27.3

---

## [0.27.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.27.2) - 2025-02-07

## What's Changed
* fix: Fix extra dependencies by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2306


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.27.1...0.27.2

---

## [0.27.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.27.1) - 2025-02-06

## What's Changed
* Support isort 6 by @cjwatson in https://github.com/koxudaxi/datamodel-code-generator/pull/2289
* fix: version CLI showing 0.0.0  by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2305

## New Contributors
* @cjwatson made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2289

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.27.0...0.27.1

---

## [0.27.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.27.0) - 2025-02-06

## What's Changed
* Migrate to use uv instead of poetry by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2288
* Add tox configuration file by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2292
* Fix collapse root model when list contains field with constraints #2067 by @dpeachey in https://github.com/koxudaxi/datamodel-code-generator/pull/2293
* Add cloudcoil by @sambhav in https://github.com/koxudaxi/datamodel-code-generator/pull/2291
* fix: tox fix environment failing by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2296
* Migrate mypy to pyright by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2295
* fix: update type-checker config by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2298
* fix: Parser crashes when input contains duplicate schemas at root level by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2301
* fix: merge test coverage before upload by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2299
* setup-trusted-publisher by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2303
* Fix custom package install in CI is not working by @gaborbernat in https://github.com/koxudaxi/datamodel-code-generator/pull/2302

## New Contributors
* @gaborbernat made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2288
* @dpeachey made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2293
* @sambhav made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2291

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.26.5...0.27.0

---

## [0.26.5](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.26.5) - 2025-01-14

## What's Changed
* typo: Fix 'Allow to' sentence. by @jas4711 in https://github.com/koxudaxi/datamodel-code-generator/pull/2240
* Fix pyproject.toml detection when `[tool.black]` section is omitted by @otonnesen in https://github.com/koxudaxi/datamodel-code-generator/pull/2242
* Update readme by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2258
* Escape null character by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2257
* enum find member quote handling by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2259
* fix Literal imports by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2260

## New Contributors
* @jas4711 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2240
* @otonnesen made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2242

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.26.4...0.26.5

---

## [0.26.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.26.4) - 2024-12-15

## What's Changed
* remove poetry-lock in .pre-commit-config.yaml by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2184
* Add --no-alias by @nickyoung-github in https://github.com/koxudaxi/datamodel-code-generator/pull/2183
* fix: Skip empty files by @jackylamhk in https://github.com/koxudaxi/datamodel-code-generator/pull/2157
* fix(parser): custom_template_dir not passed to data_model_type for OpenAPIScope.Parameters by @hambergerpls in https://github.com/koxudaxi/datamodel-code-generator/pull/2166
* feat: add support to msgspec for kw_only=True by @indrat in https://github.com/koxudaxi/datamodel-code-generator/pull/2162
* fix: Preserve class names when generating models from JSON Schema 202… by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2185
* docs: add `uri` as a supported data type by @joscha in https://github.com/koxudaxi/datamodel-code-generator/pull/2217
* Fix OpenAPI test case failure in fresh clone by @ncoghlan in https://github.com/koxudaxi/datamodel-code-generator/pull/2214
* feat: add test for Python3.13 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2219
* Update GitHub Actions to use latest action versions by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2222
* Add support for Python 3.13 in project metadata by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2223
* Default datetime class and python version by @thorwhalen in https://github.com/koxudaxi/datamodel-code-generator/pull/2196
* Avoid sharing extra template state between models by @ncoghlan in https://github.com/koxudaxi/datamodel-code-generator/pull/2215
* fix: OpenAPI 3.1: `required` with `type: [array, null]` by @joscha in https://github.com/koxudaxi/datamodel-code-generator/pull/2216
* feat: support InputFileType.Json and InputFileType.Dict by @XieJiSS in https://github.com/koxudaxi/datamodel-code-generator/pull/2165

## New Contributors
* @nickyoung-github made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2183
* @jackylamhk made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2157
* @hambergerpls made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2166
* @joscha made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2217
* @ncoghlan made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2214
* @thorwhalen made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2196
* @XieJiSS made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2165

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.26.3...0.26.4

---

## [0.26.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.26.3) - 2024-11-10

## What's Changed
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2096
* feat: add msgspec support for `--output-datetime-class datetime` by @indrat in https://github.com/koxudaxi/datamodel-code-generator/pull/2124
* Support `use_enum_values` in `ConfigDict` by @nbro10 in https://github.com/koxudaxi/datamodel-code-generator/pull/2134
* [pre-commit.ci] pre-commit autoupdate by @pre-commit-ci in https://github.com/koxudaxi/datamodel-code-generator/pull/2131
* Fix discriminator mapping resolution in schemas with parent-child hierarchy by @sternakt in https://github.com/koxudaxi/datamodel-code-generator/pull/2145
* feat: msgspec discriminated unions by @indrat in https://github.com/koxudaxi/datamodel-code-generator/pull/2081
* Fix content-hash; add pre-commit-check for poetry. by @rafalkrupinski in https://github.com/koxudaxi/datamodel-code-generator/pull/2142

## New Contributors
* @nbro10 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2134
* @sternakt made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2145
* @rafalkrupinski made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2142

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.26.2...0.26.3

---

## [0.26.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.26.2) - 2024-10-17

## What's Changed
* New CLI option --output-datetime-class #1996 by @archetipo in https://github.com/koxudaxi/datamodel-code-generator/pull/2100
* dataclass generator improvements by @anis-campos in https://github.com/koxudaxi/datamodel-code-generator/pull/2102

## New Contributors
* @archetipo made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2100
* @anis-campos made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2102

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.26.1...0.26.2

---

## [0.26.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.26.1) - 2024-09-27

## What's Changed
* feat: support string format: duration for pydantic/msgspec by @indrat in https://github.com/koxudaxi/datamodel-code-generator/pull/2084
* Change ordering of actions by @prmshepherd in https://github.com/koxudaxi/datamodel-code-generator/pull/1961
* Fix an exact import bug with deeper nesting by @AniketDas-Tekky in https://github.com/koxudaxi/datamodel-code-generator/pull/2089

## New Contributors
* @prmshepherd made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1961
* @AniketDas-Tekky made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2089

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.26.0...0.26.1

---

## [0.26.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.26.0) - 2024-09-02

## What's Changed
* Drop support python 3.7 on runtime by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2060
* Change default target python version to 3.8 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2061
* Add command help update script by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2064
* Support union_mode by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2065
* Fix default encoding as_utf-8 on command help by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2066
* Fix --use-annotated for pydantic v2 by @bpsoos in https://github.com/koxudaxi/datamodel-code-generator/pull/2033
* fix missing field descriptions in graphql by @benobytes in https://github.com/koxudaxi/datamodel-code-generator/pull/2074


## Breaking Changes
* Drop support python 3.7 on runtime by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2060
* Change default target python version to 3.8 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/2061

## New Contributors
* @benobytes made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2074

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.25.9...0.26.0

---

## [0.25.9](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.25.9) - 2024-08-07

## What's Changed
* Fix merging config and args by @mahdilamb in https://github.com/koxudaxi/datamodel-code-generator/pull/2015
* Fix missing imports for collapsed models by @kmichel-aiven in https://github.com/koxudaxi/datamodel-code-generator/pull/2043
* Don't generate files without model by @kmichel-aiven in https://github.com/koxudaxi/datamodel-code-generator/pull/2044
* Escaping apostophes in mark-down by @ben05allen in https://github.com/koxudaxi/datamodel-code-generator/pull/2047
* Typo: Fix missing whitespace in CLI help by @meliache in https://github.com/koxudaxi/datamodel-code-generator/pull/2053

## New Contributors
* @mahdilamb made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2015
* @ben05allen made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2047
* @meliache made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2053

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.25.8...0.25.9

---

## [0.25.8](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.25.8) - 2024-07-04

## What's Changed
* feat: new cli option --use-exact-imports by @alpoi-x in https://github.com/koxudaxi/datamodel-code-generator/pull/1983
* patch pydantic v1.10.9 conflict with py3.12 by @luca-knaack-webcom in https://github.com/koxudaxi/datamodel-code-generator/pull/2014
* feature: dots in paths by @luca-knaack-webcom in https://github.com/koxudaxi/datamodel-code-generator/pull/2008
* docs: Update airbyte use case + fix broken link by @natikgadzhi in https://github.com/koxudaxi/datamodel-code-generator/pull/2021
* Fix Missing Imports by @luca-knaack-webcom in https://github.com/koxudaxi/datamodel-code-generator/pull/2009
* 🚑 fixes graphql parser --use-standard-collections --use-union-operator --use-annotated by @bpsoos in https://github.com/koxudaxi/datamodel-code-generator/pull/2016

## New Contributors
* @alpoi-x made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1983
* @natikgadzhi made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2021
* @bpsoos made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/2016

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.25.7...0.25.8

---

## [0.25.7](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.25.7) - 2024-06-11

## What's Changed
* Add from_attributes to ConfigDict by @michael2to3 in https://github.com/koxudaxi/datamodel-code-generator/pull/1946
* Fix msgspec template to add field by @ianbuss in https://github.com/koxudaxi/datamodel-code-generator/pull/1942
* Add regex_engine="python-re" if regex uses lookaround by @camillol in https://github.com/koxudaxi/datamodel-code-generator/pull/1945
* Fix broken unittest by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1987
* fix: Fix subschema array items with oneOf showing up as Any type by @jdweav in https://github.com/koxudaxi/datamodel-code-generator/pull/1962
* Fix reuse_models not using the custom_template_dir by @atti92 in https://github.com/koxudaxi/datamodel-code-generator/pull/1954
* Fix alias for superclass with identical name by @kmichel-aiven in https://github.com/koxudaxi/datamodel-code-generator/pull/1981
* feat: support for external referenced discriminators by @luca-knaack-webcom in https://github.com/koxudaxi/datamodel-code-generator/pull/1991
* fix: external references to parent folder by @luca-knaack-webcom in https://github.com/koxudaxi/datamodel-code-generator/pull/1999

## New Contributors
* @michael2to3 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1946
* @ianbuss made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1942
* @camillol made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1945
* @jdweav made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1962
* @kmichel-aiven made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1981
* @luca-knaack-webcom made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1991

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.25.6...0.25.7

---

## [0.25.6](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.25.6) - 2024-04-25

## What's Changed
* Fix missing Union by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1905
* fix(ModuleImports): This PR fixes the module import resolver for module of type `BaseClassDataType` by @rshah-evertz in https://github.com/koxudaxi/datamodel-code-generator/pull/1862
* Support http query parameters by @kevin-lithic in https://github.com/koxudaxi/datamodel-code-generator/pull/1911
* feat: add examples to meta fields by @nampereira in https://github.com/koxudaxi/datamodel-code-generator/pull/1899
* Fix Rootmodel template with reuse-model by @atti92 in https://github.com/koxudaxi/datamodel-code-generator/pull/1902
* Support pendulum by @kevin-lithic in https://github.com/koxudaxi/datamodel-code-generator/pull/1914

## New Contributors
* @rshah-evertz made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1862
* @kevin-lithic made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1911
* @nampereira made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1899
* @atti92 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1902

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.25.5...0.25.6

---

## [0.25.5](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.25.5) - 2024-03-16

## What's Changed
* Fix overriden required by @fft001 in https://github.com/koxudaxi/datamodel-code-generator/pull/1868
* Added support for passing pathlib.Path as a format to JSON schema by @brandonsorensen in https://github.com/koxudaxi/datamodel-code-generator/pull/1879
* Make discriminators work with multiple keys pointing to the same schema by @ldej in https://github.com/koxudaxi/datamodel-code-generator/pull/1885

## New Contributors
* @fft001 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1868
* @brandonsorensen made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1879
* @ldej made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1885

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.25.4...0.25.5

---

## [0.25.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.25.4) - 2024-02-13

## What's Changed
* Fix object instance comparison. Add test. by @jamesbezuk in https://github.com/koxudaxi/datamodel-code-generator/pull/1849
* Added graphql to `--input-file-type` options by @JoeHCQ1 in https://github.com/koxudaxi/datamodel-code-generator/pull/1846
* Add codespell configuration, workflow, pre-commit config and fix few typos by @yarikoptic in https://github.com/koxudaxi/datamodel-code-generator/pull/1842
* has_constraints should return true for falsy values by @ericvandever in https://github.com/koxudaxi/datamodel-code-generator/pull/1844
* Fix black module error when 19.10b0 is installed by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1855

## New Contributors
* @jamesbezuk made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1849
* @JoeHCQ1 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1846
* @yarikoptic made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1842
* @ericvandever made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1844

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.25.3...0.25.4

---

## [0.25.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.25.3) - 2024-02-01

## What's Changed
* Enable ability to use custom `JsonSchemaObject` in `JsonSchemaParser` by @WilliamJamieson in https://github.com/koxudaxi/datamodel-code-generator/pull/1783
* Stop modifying behavior of yaml on load by @gluap in https://github.com/koxudaxi/datamodel-code-generator/pull/1826
* Fix bug with oneOf and const #1787 by @shadchin in https://github.com/koxudaxi/datamodel-code-generator/pull/1791
* format: support black >=24 by @airwoodix in https://github.com/koxudaxi/datamodel-code-generator/pull/1829

## New Contributors
* @WilliamJamieson made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1783
* @gluap made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1826
* @shadchin made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1791

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.25.2...0.25.3

---

## [0.25.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.25.2) - 2023-12-21

## What's Changed
* Fix original_name validation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1755
* Use const as default by @mmwinther in https://github.com/koxudaxi/datamodel-code-generator/pull/1767
* Support json_schema_extra by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1782
* Improve json_schema_extra to pydanticv2 Field with readOnly/writeOnly  by @mikedavidson-evertz in https://github.com/koxudaxi/datamodel-code-generator/pull/1778
* Fix un-imported literal when generate discriminator by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1765

## New Contributors
* @mikedavidson-evertz made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1778

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.25.1...0.25.2

---

## [0.25.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.25.1) - 2023-11-26

## What's Changed
* Fix invalid definition of graphql requirement  by @denisart in https://github.com/koxudaxi/datamodel-code-generator/pull/1741


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.25.0...0.25.1

---

## [0.25.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.25.0) - 2023-11-25

## What's Changed
* Improve collapse_root_models for list by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1700
* Improve one of any of models creation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1682
* Change example to examples in pydantic_v2 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1705
* Enable --set-default-enum-member for dataclasses by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1706
* Fix --use-default of allOf by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1708
* Beta version graphql by @denisart in https://github.com/koxudaxi/datamodel-code-generator/pull/1707
* Add a few tests graphql by @denisart in https://github.com/koxudaxi/datamodel-code-generator/pull/1722
* Add additional imports to cli by @denisart in https://github.com/koxudaxi/datamodel-code-generator/pull/1723
* Fix long description rendering in graphql by @denisart in https://github.com/koxudaxi/datamodel-code-generator/pull/1730
* Add custom formatters by @denisart in https://github.com/koxudaxi/datamodel-code-generator/pull/1733
* with pydantic v2, use pydantic.AwareDatetime instead of datetime by @gjcarneiro in https://github.com/koxudaxi/datamodel-code-generator/pull/1735
* Support discriminators in array items by @mesfahanisimscale in https://github.com/koxudaxi/datamodel-code-generator/pull/1458
* Fix default for annotated field in pydantic v2 by @i404788 in https://github.com/koxudaxi/datamodel-code-generator/pull/1498

## Breaking Changes
* Remove the unneeded `Item` suffix of `anyOf` and `oneOf` model names by https://github.com/koxudaxi/datamodel-code-generator/pull/1682
  * maybe change other model names by the changes
* Change `datetime.datetime` to `pydantic.AwareDatetime` in pydantic v2 by https://github.com/koxudaxi/datamodel-code-generator/pull/1735

## New Contributors
* @denisart made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1707
* @gjcarneiro made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1735
* @mesfahanisimscale made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1458

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.24.2...0.25.0

---

## [0.24.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.24.2) - 2023-11-16

## What's Changed
* Fix join_url bug when httpx 2.4.0 or later by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1699


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.24.1...0.24.2

---

## [0.24.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.24.1) - 2023-11-16

## What's Changed
* Add pyyaml by @mmwinther in https://github.com/koxudaxi/datamodel-code-generator/pull/1698

## New Contributors
* @mmwinther made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1698

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.24.0...0.24.1

---

## [0.24.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.24.0) - 2023-11-16

## What's Changed
* Fix UnionIntFloat json schema generation. by @jboulmier in https://github.com/koxudaxi/datamodel-code-generator/pull/1669
* Move pysnooper to optional by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1672
* use tomllib in 3.11 or later by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1674
* Change --validation option to optional by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1675
* Remove black in lint.sh and format.sh by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1676
* Resolve ref before adding references for allOf items by @pimzero in https://github.com/koxudaxi/datamodel-code-generator/pull/1678
* Change master to main by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1681
* Fix error when JsonSchemaObject is bool with Pydantic v2 by @sbussard-vareto in https://github.com/koxudaxi/datamodel-code-generator/pull/1691

## Breaking Changes
* The version drop  `pysnooper`, `prance`, `openapi-spec-validator` from the default dependencies list.
If you want to use `--debug` and ` --validation`, please add the extra option when you install packages `datamodel-code-generator[debug]` and `datamodel-code-generator[validation]`

## Depreacated
* `--validation` option will be removed in the new future. please use other tools to validate OpenAPI Schema.

## New Contributors
* @jboulmier made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1669
* @pimzero made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1678
* @sbussard-vareto made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1691

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.23.0...0.24.0

---

## [0.23.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.23.0) - 2023-11-08

## What's Changed
* Support $defs for Draft 2019-09  by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1630
* Update openapi-spec-validator max version by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1633
* Fix list default in dataclass by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1632
* Support python 3.12 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1612
* Fix `--remove-special-field-name-prefix` + fields (e.g. enum members) starting with numbers by @lord-haffi in https://github.com/koxudaxi/datamodel-code-generator/pull/1654
* Fix generation of nullable array items by @tfausten in https://github.com/koxudaxi/datamodel-code-generator/pull/1648
* Move nullable logic to openapi parser by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1655
* Add missing space to README.md by @joakimnordling in https://github.com/koxudaxi/datamodel-code-generator/pull/1660
* Improve model naming by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1663
* Fix exponent in minimum by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1664
* Remove constraint on anyurl by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1665
* Improve discriminator by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1666

## New Contributors
* @lord-haffi made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1654
* @tfausten made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1648
* @joakimnordling made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1660

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.22.1...0.23.0

---

## [0.22.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.22.1) - 2023-10-08

## What's Changed
* Ignore broken pydantic version 2.4.0 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1593
* Avoid pydantic ClassVar bug by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1603
* Fix msgspec pattern and optional annotated type by @indrat in https://github.com/koxudaxi/datamodel-code-generator/pull/1606
* Fix missing constr import issue by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1609
* Fix msgspec root import by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1611


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.22.0...0.22.1

---

## [0.22.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.22.0) - 2023-09-23

## What's Changed
* Organize help options by @howardj99 in https://github.com/koxudaxi/datamodel-code-generator/pull/1540
* fix: add type parameters to generic RootModel by @tcrasset in https://github.com/koxudaxi/datamodel-code-generator/pull/1560
* chore: link to contributing guidelines from README.md by @tcrasset in https://github.com/koxudaxi/datamodel-code-generator/pull/1561
* Fix base path to avoid duplicate parts in path when deleting reference by @pedrosmr in https://github.com/koxudaxi/datamodel-code-generator/pull/1550
* Support msgspec output by @indrat in https://github.com/koxudaxi/datamodel-code-generator/pull/1551

## New Contributors
* @howardj99 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1540
* @tcrasset made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1560
* @pedrosmr made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1550
* @indrat made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1551

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.21.5...0.22.0

---

## [0.21.5](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.21.5) - 2023-09-06

## What's Changed
* Added protect namespace to Pydantic v2 ConfigDict by @willarmiros in https://github.com/koxudaxi/datamodel-code-generator/pull/1489
* Fix pydantic 2.2.1 RootModel cannot have extra_fields error by @i404788 in https://github.com/koxudaxi/datamodel-code-generator/pull/1497
* Add missing default=None by @twoodwark in https://github.com/koxudaxi/datamodel-code-generator/pull/1515
* Fix class Field generated when array with name fields with oneOf inside by @mjperrone in https://github.com/koxudaxi/datamodel-code-generator/pull/1516

## New Contributors
* @willarmiros made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1489
* @twoodwark made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1515
* @mjperrone made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1516

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.21.4...0.21.5

---

## [0.21.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.21.4) - 2023-08-09

## What's Changed
* Update openapi-spec-validator to 0.5.7 by @der-eismann in https://github.com/koxudaxi/datamodel-code-generator/pull/1475
* Fix Pydantic V2 RootModel inheritance by @barreeeiroo in https://github.com/koxudaxi/datamodel-code-generator/pull/1477
* main: fix configuration model validation after CLI args merge by @airwoodix in https://github.com/koxudaxi/datamodel-code-generator/pull/1448
* Fix const for pydantic_v2 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1482
* Unique list is defined as list by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1484
* switch away from deprecated pydantic method by @iliakur in https://github.com/koxudaxi/datamodel-code-generator/pull/1485
* Fix condecimal by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1487
* Add unittest for discriminator by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1488

## New Contributors
* @der-eismann made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1475
* @barreeeiroo made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1477
* @iliakur made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1485

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.21.3...0.21.4

---

## [0.21.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.21.3) - 2023-08-03

## What's Changed
* Give Pydantic V2 its own `dump_resolve_reference_action` to use `model_rebuild` not `update_forward_refs` by @lmmx in https://github.com/koxudaxi/datamodel-code-generator/pull/1468
* fix: Fix regex expression of hostname in pydantic v2 by @xu-cheng in https://github.com/koxudaxi/datamodel-code-generator/pull/1449
* Add `base_class` validation similar to `_validate_base_class` to bare `generate` calls by @kylebebak in https://github.com/koxudaxi/datamodel-code-generator/pull/1453
* Fix issue #1461: Open API parameter models use base class if set by @piercsi in https://github.com/koxudaxi/datamodel-code-generator/pull/1462
* Issues 1454 typeddict not required optional bug fix by @kylebebak in https://github.com/koxudaxi/datamodel-code-generator/pull/1457

## New Contributors
* @lmmx made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1468
* @xu-cheng made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1449
* @kylebebak made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1453
* @piercsi made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1462

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.21.2...0.21.3

---

## [0.21.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.21.2) - 2023-07-20

## What's Changed
* Pre-commit-hooks by @yehoshuadimarsky in https://github.com/koxudaxi/datamodel-code-generator/pull/1416
* feat: add additional imports by @skonik in https://github.com/koxudaxi/datamodel-code-generator/pull/1422
* Add orm_mode to Config(BaseModel)  by @Beaueve in https://github.com/koxudaxi/datamodel-code-generator/pull/1425
* Fix "parent" AttributeError when calling --collapse-root-models by @imankulov in https://github.com/koxudaxi/datamodel-code-generator/pull/1432

## New Contributors
* @yehoshuadimarsky made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1416
* @skonik made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1422
* @Beaueve made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1425
* @imankulov made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1432

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.21.1...0.21.2

---

## [0.21.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.21.1) - 2023-07-06

## What's Changed
* Relax version constraint of prance by @tetsuok in https://github.com/koxudaxi/datamodel-code-generator/pull/1409
* Fix optional field doesn't include `None` by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1411

## New Contributors
* @tetsuok made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1409

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.21.0...0.21.1

---

## [0.21.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.21.0) - 2023-07-03

## What's Changed
* pyproject: correct poetry-core package name by @ConnorBaker in https://github.com/koxudaxi/datamodel-code-generator/pull/1374
* Support pydantic v2 in runtime by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1392
* Add pydantic v2 as output model type by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1391
* Add --use-unique-items as set by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1400

## New Contributors
* @ConnorBaker made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1374

# What is the difference between pydantic v1 and v2 output model? 

## Summary
datamodel-code-generator supports Pydantic v1 and v2 as output model type.

Pydantic v2 is a major release with many breaking changes. See the migration guide for more information:
https://docs.pydantic.dev/2.0/migration/

## What's changes in v2 output?
### `__root__` field (a.k.a [Custom Root Types](https://docs.pydantic.dev/1.10/usage/models/#custom-root-types))
`__root__` field (a.k.a [Custom Root Types](https://docs.pydantic.dev/1.10/usage/models/#custom-root-types)) is removed in pydantic v2.
The model is changed to [RootModel](https://docs.pydantic.dev/latest/usage/models/#rootmodel-and-custom-root-types)

### pydantic.Field
https://docs.pydantic.dev/2.0/migration/#changes-to-pydanticfield

- const -> removed
- min_items (use min_length instead)
- max_items (use max_length instead)
- unique_items -> removed and the list type will be replaced by `typing.Set`. this feature is discussed in https://github.com/pydantic/pydantic-core/issues/296
- allow_mutation (use frozen instead)
- regex (use pattern instead)

### Model Config
- `pydantic.Config` -> `pydantic.ConfigDict` 
- allow_mutation —> frozen (inverse value for getting same behavior).
- allow_population_by_field_name → populate_by_name



**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.20.0...0.21.0

---

## [0.20.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.20.0) - 2023-06-06

## What's Changed
* Update documentation by @noddycode in https://github.com/koxudaxi/datamodel-code-generator/pull/1316
* Support TypedDict as output type by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1309
* Remove self attribute from jsonschema root attributes by @rezen in https://github.com/koxudaxi/datamodel-code-generator/pull/1318
* Fix $ref With Path Items by @zach-hamm in https://github.com/koxudaxi/datamodel-code-generator/pull/1323
* Remove keep-runtime-typing on ruff config by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1349
* Update `openapi-spec-validator` to `0.5.2` by @Fokko in https://github.com/koxudaxi/datamodel-code-generator/pull/1343
* Added support for model wise base classes by @senesh-deshan in https://github.com/koxudaxi/datamodel-code-generator/pull/1350
* Fix the custom file header by @Fokko in https://github.com/koxudaxi/datamodel-code-generator/pull/1346

## Breaking Changes
* Eliminate the naming differences of model names and field names by the OS by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1348

Due to differences in file loading order between operating systems, we've added a sorting process when fetching file listings within directories in order to eliminate discrepancies in model and field names. As a result, model names that differ from those generated by existing earlier versions may be produced.

## New Contributors
* @noddycode made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1316
* @rezen made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1318
* @zach-hamm made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1323
* @Fokko made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1343
* @senesh-deshan made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1350

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.19.0...0.20.0

---

## [0.19.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.19.0) - 2023-05-10

## What's Changed
* Import Annotated from typing_extensions for Python <= 3.8 by @airwoodix in https://github.com/koxudaxi/datamodel-code-generator/pull/1274
* Fix OpenAPI array response by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1261
* Fix unresolved nested ref in openapi by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1269
* Fix unsorted dataclass field by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1288
* Fix allOf-anyOf by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1291
* Fix null by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1299

## New Contributors
* @airwoodix made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1274

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.18.1...0.19.0

---

## [0.18.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.18.1) - 2023-04-27

## What's Changed
* default_factory field should be non-optional by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1254
* add --use-one-literal-as-default by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1256
* (🎁) Include `py.typed` to mark project as typed by @KotlinIsland in https://github.com/koxudaxi/datamodel-code-generator/pull/1259
* Fix min/max on number fields truncated by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1266
* Add use_operation_id_as_name by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1267
* Add datetime to number and integer by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1268


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.18.0...0.18.1

---

## [0.18.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.18.0) - 2023-04-16

## What's Changed
* Update __all__ to expose needed types by @ShaneMurphy2 in https://github.com/koxudaxi/datamodel-code-generator/pull/1230
* Fix max min round issue by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1235
* Fix strict nullable for default factory by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1236
* Improve union types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1241
* Support nested all_of by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1242
* (🎁) bump some dependencies by @KotlinIsland in https://github.com/koxudaxi/datamodel-code-generator/pull/1245
* (🎁) log file type when `--input-file-type` is auto. by @KotlinIsland in https://github.com/koxudaxi/datamodel-code-generator/pull/1248
* (🎁) remove typed-ast dependency by @KotlinIsland in https://github.com/koxudaxi/datamodel-code-generator/pull/1246
* (🎁) reference config option to specify input type by @KotlinIsland in https://github.com/koxudaxi/datamodel-code-generator/pull/1250
* (🎁) infer data vs schema when `--input-file-type` is auto by @KotlinIsland in https://github.com/koxudaxi/datamodel-code-generator/pull/1249

## New Contributors
* @ShaneMurphy2 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1230

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.17.2...0.18.0

---

## [0.17.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.17.2) - 2023-03-31

## What's Changed
* Remove union operator error when target-python is 3.9 or early version by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1094
* Add ruff by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1095
* Fix only required anyof oneof by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1104
* Update mypy version to 1.0.0 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1105
* fix mypy version to >=1.0.1,<1.1.0 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1162
* Docs: typo by @KPLauritzen in https://github.com/koxudaxi/datamodel-code-generator/pull/1153
* Fix typo: `docuemnt` -> `document` by @kamyar in https://github.com/koxudaxi/datamodel-code-generator/pull/1179
* feat: add support for custom file header by @Niraj-Kamdar in https://github.com/koxudaxi/datamodel-code-generator/pull/1164
* Exclude fields with default_factory from --use-default-kwarg by @vogre in https://github.com/koxudaxi/datamodel-code-generator/pull/1186
* Bugfix: with deeply nested modules: empty paths did not get an __init__.py file created by @dataway in https://github.com/koxudaxi/datamodel-code-generator/pull/1187
* Fix url $id by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1214
* (🎁) deps: make typed-ast optional by @KotlinIsland in https://github.com/koxudaxi/datamodel-code-generator/pull/1222

## New Contributors
* @KPLauritzen made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1153
* @kamyar made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1179
* @Niraj-Kamdar made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1164
* @vogre made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1186
* @dataway made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1187
* @KotlinIsland made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1222

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.17.1...0.17.2

---

## [0.17.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.17.1) - 2023-02-06

## What's Changed
* Change custom template dir structure by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1070
* Support enum as literal in root model by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1073
* Improve duplicate model detection by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1076
* Fix reference same hierarchy directory by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1082
* Fix wrong overwrite field default by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1085
* Fix mro field name on enum by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1069
* OpenAPI - Add support for query parameter model generation by @hambrosia in https://github.com/koxudaxi/datamodel-code-generator/pull/1083
* Support dataclasses.dataclass by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1068

## New Contributors
* @hambrosia made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1083

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.17.0...0.17.1

---

## [0.17.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.17.0) - 2023-01-30

## What's Changed
* Add `--keep-model-order` by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1043
* Support openapi `discriminator` by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1045
* Fix referenced `patternProperties` by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1046
* Support `default_factory` by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1047
* Remove duplicate model by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1048
* Fix naming logic for duplicate name model by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1049
* Proposal for adding version metadata to header section by @ghandic in https://github.com/koxudaxi/datamodel-code-generator/pull/1053
* Refactor set-default-enum-member by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1057
* Support list  default enum member by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1058
* Support default object value by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1060
* Fix cli option name --use_non_positive_negative_number_constrained_types by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1064
* Add `--field-extra-keys-without-x-prefix` by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1066

## New Contributors
* @ghandic made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1053

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.16.1...0.17.0

---

## [0.16.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.16.1) - 2023-01-22

## What's Changed
* Change default value to None by @JensHeinrich in https://github.com/koxudaxi/datamodel-code-generator/pull/1028
* Support json pointer to array by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1030
* Fix/remove not none arg parse defaults by @JensHeinrich in https://github.com/koxudaxi/datamodel-code-generator/pull/1029
* Add --use-default-kwarg option for better type checker compatibility by @zackyancey in https://github.com/koxudaxi/datamodel-code-generator/pull/1034
* add --remove-special-field-name-prefix by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1033
* Fix snake_case generation by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1037
* Add --disable-warnings by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1038
* Remove dummy field by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1039

## New Contributors
* @JensHeinrich made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1028
* @zackyancey made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1034

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.16.0...0.16.1

---

## [0.16.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.16.0) - 2023-01-16

## What's Changed
* Fix the order of the model having multiple base_class by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/995
* Fix incorrect remove model action by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/997
* Support reference to object properties by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/998
* Fix field constraint number coersion bug by @ninowalker in https://github.com/koxudaxi/datamodel-code-generator/pull/1003
* Fix --collapse-root-models by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1002
* Fix --capitalise-enum-members by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1011
* Override optional all_of field with required by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1012
* Improve anyOf and oneOf detection when anyOf and oneOf parent has proprerties by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/1013

## New Contributors
* @ninowalker made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/1003

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.15.0...0.16.0

---

## [0.15.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.15.0) - 2023-01-04

## Notice
The release has some improvements in model generation.
**The generated model names or the structure will be changed.**

## What's Changed
* Remove unneeded number suffix when the same name model exists in another module by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/973
* Ignore duplicate name field on allOf by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/974
* Improve model deletion by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/976
* Refactor process after parse by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/977
* Support JSONSchema items is boolean by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/979
* Support required in allof item by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/981
* Improve detection additionalProperties in JSONSchema Object by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/982
* Fix nullable object by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/987
* Support const by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/989
* Fix duplicated nested optional by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/990

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.14.1...0.15.0

---

## [0.14.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.14.1) - 2022-12-28

## What's Changed
* Fix deprecation warnings around "copy_on_model_validation" by @Dominic-Walther in https://github.com/koxudaxi/datamodel-code-generator/pull/927
* Fix dev black version for macos by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/941
* Add support for tags and include responses without content by @Aedial in https://github.com/koxudaxi/datamodel-code-generator/pull/924
* Fix indents for multi-line docstrings by @Dominic-Walther in https://github.com/koxudaxi/datamodel-code-generator/pull/938
* Allow to pass extra fields using `--allow-extra-fields` by @fgebhart in https://github.com/koxudaxi/datamodel-code-generator/pull/949
* Fix resolving id by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/963
* fix: template handling of extra template data by @auphofBSF in https://github.com/koxudaxi/datamodel-code-generator/pull/861
* Avoid field name beginning with an underscore by @ronlib in https://github.com/koxudaxi/datamodel-code-generator/pull/962
* Support --special-field-name-prefix option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/966
* Fix allOf with common prefix and $ref by @azatoth in https://github.com/koxudaxi/datamodel-code-generator/pull/968
* Update poetry.lock by @fsecada01 in https://github.com/koxudaxi/datamodel-code-generator/pull/936
* Add collapse root model feature by @i404788 in https://github.com/koxudaxi/datamodel-code-generator/pull/933
* add --capitalise-enum-members option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/970
* Fix no generated enum in array by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/971

## New Contributors
* @Aedial made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/924
* @fgebhart made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/949
* @lgtm-com made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/908
* @auphofBSF made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/861
* @ronlib made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/962
* @azatoth made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/968
* @fsecada01 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/936
* @i404788 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/933

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.14.0...0.14.1

---

## [0.14.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.14.0) - 2022-11-18

## What's Changed
* Drop python3.6 support by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/887
* Support Python 3.11 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/898
* Show help when no input by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/911
* Add docker image by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/919
* Implement field descriptions as docstrings by @Dominic-Walther in https://github.com/koxudaxi/datamodel-code-generator/pull/918
* Fix push docker flow by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/925

## New Contributors
* @Dominic-Walther made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/918

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.13.5...0.14.0

---

## [0.13.5](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.13.5) - 2022-11-06

## What's Changed
* Fix mro field on enum by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/891
* Fix openapi definitions schema by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/897
* Fix generating import statement code by @antxln in https://github.com/koxudaxi/datamodel-code-generator/pull/893

## New Contributors
* @antxln made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/893

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.13.4...0.13.5

---

## [0.13.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.13.4) - 2022-10-31

## What's Changed
* Fix alias modular default enum member by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/888


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.13.3...0.13.4

---

## [0.13.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.13.3) - 2022-10-27

## What's Changed
* Fix modular default enum member by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/876
* Use format of object if provided by @pn in https://github.com/koxudaxi/datamodel-code-generator/pull/874
* Support union operator `|` by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/884

## New Contributors
* @pn made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/874

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.13.2...0.13.3

---

## [0.13.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.13.2) - 2022-10-17

## What's Changed
* Use default of $ref on enum by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/832
* Fix class name generator by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/841
* Replace exponential in name for FieldnameResolver by @PTank in https://github.com/koxudaxi/datamodel-code-generator/pull/833
* Support discriminators by @bernardoVale in https://github.com/koxudaxi/datamodel-code-generator/pull/838
* add pre-commit for black and isort by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/865
* Support Boolean property by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/872
* fix(format): Fix PythonVersion.has_literal_type for Python 3.10. by @pawelrubin in https://github.com/koxudaxi/datamodel-code-generator/pull/868

## New Contributors
* @PTank made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/833
* @bernardoVale made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/838
* @pre-commit-ci made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/867
* @pawelrubin made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/868

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.13.1...0.13.2

---

## [0.13.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.13.1) - 2022-08-11

## What's Changed
* Added IPv4/IPv6Network support. by @ngaranko in https://github.com/koxudaxi/datamodel-code-generator/pull/789
* added option --use-double-quotes by @nesb1 in https://github.com/koxudaxi/datamodel-code-generator/pull/818
* Fix deep copy max recursion failure - pydantic 1.9.1 by @eyalmor-ent in https://github.com/koxudaxi/datamodel-code-generator/pull/819

## New Contributors
* @ngaranko made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/789
* @nesb1 made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/818
* @eyalmor-ent made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/819

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.13.0...0.13.1

---

## [0.13.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.13.0) - 2022-05-27

## What's Changed
* Fix --snake-case-field breaks class_name by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/777


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.12.3...0.13.0

---

## [0.12.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.12.3) - 2022-05-27

## What's Changed
* Support --original-field-name-delimiter option by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/776


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.12.2...0.12.3

---

## [0.12.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.12.2) - 2022-05-27

## What's Changed
* Support subclass enum by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/771


**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.12.1...0.12.2

---

## [0.12.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.12.1) - 2022-05-19

## What's Changed
* [FIX] inheritance update refs by @jdkent in https://github.com/koxudaxi/datamodel-code-generator/pull/767
* Add Python 3.10 to PythonVersion by @sgaist in https://github.com/koxudaxi/datamodel-code-generator/pull/765

## New Contributors
* @jdkent made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/767
* @sgaist made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/765

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.12.0...0.12.1

---

## [0.12.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.12.0) - 2022-04-18

## What's Changed
* Fix field constraint value by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/745
* Correct typo in GitHub Actions black versions by @lafrenierejm in https://github.com/koxudaxi/datamodel-code-generator/pull/744
* Support unique_items by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/746
* Fix nested Enum by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/747

## New Contributors
* @lafrenierejm made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/744

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.11.20...0.12.0

---

## [0.11.20](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.20) - 2022-03-12

## What's Changed
* no use constr when field-constraints by @linshoK in https://github.com/koxudaxi/datamodel-code-generator/pull/726

## New Contributors
* @linshoK made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/726

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.11.19...0.11.20

---

## [0.11.19](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.19) - 2022-02-08

## What's Changed
* fix booleans literals being converted to int by @koonpeng in https://github.com/koxudaxi/datamodel-code-generator/pull/704
* feat: Add option to disable tls verification in http request by @jtfidje in https://github.com/koxudaxi/datamodel-code-generator/pull/707

## New Contributors
* @koonpeng made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/704
* @jtfidje made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/707

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.11.18...0.11.19

---

## [0.11.18](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.18) - 2022-02-02

## What's Changed
* [Docs] Fix typo by @ijrsvt in https://github.com/koxudaxi/datamodel-code-generator/pull/702
* [Fix] Support Black 22.1.0 by @ijrsvt in https://github.com/koxudaxi/datamodel-code-generator/pull/701

## New Contributors
* @ijrsvt made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/702

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.11.17...0.11.18

---

## [0.11.17](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.17) - 2022-01-23

## What's Changed
* do not convert literal int to string for pydantic 1.9.0 or later by @duesenfranz in https://github.com/koxudaxi/datamodel-code-generator/pull/689

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.11.16...0.11.17

---

## [0.11.16](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.16) - 2022-01-17

## What's Changed
* Fix json type form http by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/637
* Support Python3.10 by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/668
* GitHub Actions: Add Python 3.10 to the testing by @cclauss in https://github.com/koxudaxi/datamodel-code-generator/pull/605
* Use `Non{Positive,Negative}{Float,Int}` in models by @duesenfranz in https://github.com/koxudaxi/datamodel-code-generator/pull/679

## New Contributors
* @cclauss made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/605
* @duesenfranz made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/679

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.11.15...0.11.16

---

## [0.11.15](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.15) - 2021-11-29

## What's Changed
* Fix typed-ast by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/626
* Fix typed_ast dependency with custom mypy by @koxudaxi in https://github.com/koxudaxi/datamodel-code-generator/pull/634
* documented installation via pip by @adaamz in https://github.com/koxudaxi/datamodel-code-generator/pull/620
* Fix typo in argument description by @jacobszpz in https://github.com/koxudaxi/datamodel-code-generator/pull/606
* Fix parsing of absolute reference URLs by @vesajaaskelainen in https://github.com/koxudaxi/datamodel-code-generator/pull/594

## New Contributors
* @adaamz made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/620
* @jacobszpz made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/606
* @vesajaaskelainen made their first contribution in https://github.com/koxudaxi/datamodel-code-generator/pull/594

**Full Changelog**: https://github.com/koxudaxi/datamodel-code-generator/compare/0.11.14...0.11.15

---

## [0.11.14](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.14) - 2021-09-30

- Fix a bug for # field [#558]

---

## [0.11.13](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.13) - 2021-09-15

- Support pydantic 1.5.1 [#537]

---

## [0.11.12](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.12) - 2021-08-27

- ignore broken regex version [#519]
- Support --use-annotated [#516]
- Support --http_headers for authorization [#511]
- Add --use-title-as-name [#510]
- Support patternProperties [#509]
- Fix reuse_model for nested model [#505]
- Improve supporting json_pointer [#503]







---

## [0.11.11](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.11) - 2021-08-12

- Fix version option [#499]

---

## [0.11.10](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.10) - 2021-08-12

- Fix invalid generated code with openapi validator [#496]
- Support wrap_string_literal [#493]
- Use Poetry [#475]

---

## [0.11.9](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.9) - 2021-07-31

- Fix setup.py [#473]
- Support false on additionalProperties [#472] by @reb00ter
- Support jinja2 version3 [#468]
- Support PEP-517 [#467]
- Validate pydantic field name [#465]

## Improve OpenAPI Parser
- Refactor parse_ref [#449] 
- Fix path objects in openapi [#459]
- Fix path parameters [#460]
- Improve resolving response model [#461]
- Improve OpenAPI parser [#462]

Thanks to @reb00ter

---

## [0.11.8](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.8) - 2021-06-11

- set default base class when removed all base class [#448 ]

---

## [0.11.7](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.7) - 2021-06-09

- Fix inline enum only string literal [#445] by @goodoldneon

Thanks to @goodoldneon

---

## [0.11.6](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.6) - 2021-05-28

- Support customTypePath [#436]

---

## [0.2.11+backport-1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.11+backport-1) - 2021-05-29

- backport improve_x_enum_varnames 

---

## [0.11.5](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.5) - 2021-05-16

- Don't generate enum models that have been swapped by a reused model [#430] by @shnups 
- Pass custom template dir when creating enums [#427] by @shnups 

Thanks to @shnups

---

## [0.11.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.4) - 2021-05-05

- Handle nested nullable enums [#425] by @philipbjorge 

Thanks to @philipbjorge

---

## [0.11.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.3) - 2021-04-29

- Fix #420 [#421] by @goodoldneon 
 (IPv4Address doesn't import from pydantic.validators [#420])

Thanks to @goodoldneon 

---

## [0.11.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.2) - 2021-04-26

- Support field extras keys [#416]

---

## [0.11.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.1) - 2021-04-22

- Fix get_relative_path subfolders with same names [#415] by @siame

Thanks to @siame


---

## [0.11.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.11.0) - 2021-04-21

- Improve parsing additionalProperties [#412]
- Improve parsing complex object [#411] 

breaking change
The detail is in #413

---

## [0.10.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.10.3) - 2021-04-18

- Support complex `oneOf` and `anyOf` [#410]

---

## [0.10.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.10.2) - 2021-04-03

- Improve resolving relative path [#406]
- Refactor parse_enum [#405]

---

## [0.10.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.10.1) - 2021-03-31

- Improve imports in DataType [#403]
- Support self reference array [#402]
- Support allOf with object [#401]
- Improve generating model name [#400]

---

## [0.10.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.10.0) - 2021-03-27

- Support time and idn-email format #397
- Support custom_class_name_generator #396
- Reduce unnecessary downloading #395
- Fix resolving ref with root id #394
- Fix modular generated models #393 
- Refactor module_name #391
- Remove append_result #390
- Remove field name validator #389 
- Fix import style #388
- Fix duplicate field name #386

### breaking change
The detail is in #398

---

## [0.9.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.9.4) - 2021-03-21

- Refactor data_type_manager [#384]
- Fix to parse enum correctly [#383]
- Allow using Literal in python 3.7 via typing_extensions backport [#382] by @yuyupopo 
- Update dependencies [#378]

Thanks to @yuyupopo 

---

## [0.9.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.9.3) - 2021-03-18

- Improve resolving module names [#377]
- Refactor DataModel [#376]
- Improve code style [#375]

---

## [0.9.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.9.2) - 2021-03-11

- Improve typing [#373]
- Fix x-enum-varnames for string [#372]

---

## [0.9.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.9.1) - 2021-03-08

- Escape special characters in regex [#370]
- Support strict types [#369]
- Add --disable-appending-item-suffix [#368]
- Strip constraints on self-reference [#367] 
- Fix custom_root_model [#366]
- Support HTTP URL as an input [#365]

---

## [0.9.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.9.0) - 2021-03-04

- Fix model name which is same imported_name [#364]
- Fix broken update_forward_refs [#363]
- Improve coding style [#362]
- Change all imports return type to iterator[Import] [#361]
- Lazy creating imports and unresolved_types [#360]
- Resolve parent model by data_type [#359]
- Model properties are generated when rendering [#358]
- Refactoring model structure [#354]

breaking change
The detail is in https://github.com/koxudaxi/datamodel-code-generator/issues/357

---

## [0.8.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.8.3) - 2021-02-25

- Support nested json_pointer [#352]

---

## [0.8.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.8.2) - 2021-02-20

- Generate function supports relative input path [#351]
- Support nullable enum [#350]

---

## [0.8.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.8.1) - 2021-02-18

- Fix parsing ref on root model [#346]
- Change regex to raw strings [#345] 

---

## [0.8.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.8.0) - 2021-02-17

- Ignore duplicate object of allOf [#343]
- Improve constrained type in array [#342]
- Improve complex additional properties [#341]
- Add alias attribute on DataType [#340]

### breaking change
The detail is in #344

---

## [0.7.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.7.3) - 2021-02-16

- Support enable_faux_immutability [#338]
- Support generic_container_types [#336]
- Fix oneOf [#335]

---

## [0.7.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.7.2) - 2021-02-09

- Improve strict-nullable [#330]

---

## [0.7.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.7.1) - 2021-02-05

- Remove unused condition [#329]
- Support strict-nullable option [#328]

---

## [0.7.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.7.0) - 2021-01-31

- Fix type in anyof [#326]
- Improve resolving referenced models [#324]
- Fix wrong data type class [#322]

# breaking change
 Change internal interface [https://github.com/koxudaxi/datamodel-code-generator/issues/325]


---

## [0.6.26](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.26) - 2021-01-26

- Fix detecting allOf [#321] 

---

## [0.6.25](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.25) - 2021-01-24

- Fix importing field [#319]
- Improve resolving references [#318]

---

## [0.6.24](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.24) - 2021-01-23

- Support enum in additional properties [#316]
- Fix root_model with additional properties [#315]

---

## [0.6.23](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.23) - 2021-01-21

- Fix remote obj cache [#313]
- Parse enum field as literal [#312] 

---

## [0.6.22](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.22) - 2021-01-19

- Support json pointer [#311]
- Raise model not found exception [#310]

---

## [0.6.21](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.21) - 2021-01-18

- Support multiple type enum [#309]
- Support CSV [#308]
- Support custom encoding [#307]

---

## [0.6.20](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.20) - 2021-01-15

- Support python dictionary [#305]
- Fix parse_ref in one_of [#304[

---

## [0.6.19](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.19) - 2021-01-15

- Fix nested enum [#303]
- Fix similar nested array [#302] 

---

## [0.6.18](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.18) - 2021-01-08

- ignore datetime in yaml [#299]

---

## [0.6.17](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.17) - 2021-01-06

- improve reusing model [#297]

---

## [0.6.16](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.16) - 2020-12-31

- cache jinja2 template [#296]
- improve performance 2 [#295]

---

## [0.6.15](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.15) - 2020-12-28

- Support reuse_models [#294]
- Support Python3.9 [#293]
- Add use-schema-description option [#292] 

---

## [0.6.14](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.14) - 2020-12-27

- Fix file path on windows [#291]
- Improve performance [#290]

---

## [0.6.13](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.13) - 2020-12-25

- Fix array include null [#288]
- Fix resolving base class order [#287]
- Warn with default format for type when not found [#273] by @Liam-Deacon 

Thanks to @Liam-Deacon 

---

## [0.6.12](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.12) - 2020-12-24

- fix invalid field name on enum [#281]
- support circular reference [#280] 

---

## [0.6.11](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.11) - 2020-12-17

- Fix root array [#277]
- Fix detecting file path [#276]

---

## [0.6.10](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.10) - 2020-12-07

- Support nested directory [#275]

---

## [0.6.9](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.9) - 2020-12-02

- support external files in a directory [#272]

---

## [0.6.8](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.8) - 2020-11-29

- fix a condition of importing annotations [#270]
- remove version limit of black [#269] 
- improve $id [#261]

---

## [0.6.7](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.7) - 2020-11-17

- fix external definitions [#265]

---

## [0.6.6](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.6) - 2020-11-14

- support standard collections for type hinting (list, dict)  [#263]
- support python3.9 [#262]

---

## [0.6.5](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.5) - 2020-11-12

- support $id [#260]
- support root $id  [#259]

---

## [0.6.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.4) - 2020-11-11

- support custom class name [#257]
- ignore subclass enum [#255] 

---

## [0.6.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.3) - 2020-11-09

- fix settings for isort [#254]
- update isort and black [#251]  by @Chilipp 

Thanks to @Chilipp 

---

## [0.6.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.2) - 2020-11-05

- add `--force-optional` [#250] 
- change `use_default_on_required_field` to `apply_default_values_for_required_fields` [#249 ]

---

## [0.6.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.1) - 2020-11-01

- support input_filename [#248]
- support unix-time [#247]

---

## [0.6.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.6.0) - 2020-10-18

- Change parser interface [#241]
- fix handling field constraints [#240]
- refactor parsers [#236]

---

## [0.5.39](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.39) - 2020-10-06

- Fix arrays in additional properties [#234]
- Remove unused imports [#235]

---

## [0.5.38](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.38) - 2020-10-05

- Fix exclusiveMaximum and exclusiveMinimum [#233]

---

## [0.5.37](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.37) - 2020-10-03

- support null and array in type [#226]

---

## [0.5.36](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.36) - 2020-10-02

Fixes typo in import name of condecimal [#230] by @kriberg
Thanks to @kriberg

---

## [0.5.35](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.35) - 2020-09-23

- Use default value even if a field is required [#228]
- Fix nested additional properties [#227]

---

## [0.5.34](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.34) - 2020-09-19

- fix external root jsonschema [#221]

---

## [0.5.33](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.33) - 2020-09-17

- add a option to disable timestamp on file headers [#219]
- add allow_population_by_field_name option [#220]

---

## [0.5.32](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.32) - 2020-09-16

- Fix additional properties [#218]

---

## [0.5.31](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.31) - 2020-09-12

- Fix additional properties [#214]
- Refactor import [#213]
- Fix positive import [#212]

---

## [0.5.30](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.30) - 2020-09-04

- support any [#208]
- support example [#207]

---

## [0.5.29](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.29) - 2020-08-25

- change dependency versions [#202]

---

## [0.5.28](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.28) - 2020-08-21

- change argcomplete lowest version [#201]

---

## [0.5.27](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.27) - 2020-08-16

- Added support of integer in string [#199] by @Forden
Thanks to @Forden

---

## [0.5.26](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.26) - 2020-08-14

- update requirement versions [#198]

---

## [0.5.25](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.25) - 2020-08-13

- Fix schemas path [#196]
- Add uri-reference [#194] by @drsm79 
Thanks to @drsm79 

---

## [0.5.24](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.24) - 2020-08-03

- improve merge of CLI arguments and pyproject options [#191] by @joshbode
Thanks to @joshbode

- ignore type and sort arguments of `Field` [#192]

---

## [0.5.23](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.23) - 2020-08-02

- Support options on pyproject.toml [#190]

---

## [0.5.22](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.22) - 2020-07-30

- add strip-default-none option [#186]

---

## [0.5.21](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.21) - 2020-07-30

- add snake case field option [#185]

---

## [0.5.20](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.20) - 2020-07-27

Fix: #182. Fix yaml support. Improve OAS3  [#183] by @ioggstream

Thanks to @ioggstream

---

## [0.5.19](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.19) - 2020-07-24

- Support remote ref [#179]

---

## [0.5.18](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.18) - 2020-07-23

- Add aliases option to map reserved fields [#181] by @joshbode

Thanks to @joshbode

---

## [0.5.17](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.17) - 2020-07-22

- Fix model names( include fixing duplicate enum names) [#180]

---

## [0.5.16](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.16) - 2020-07-19

- add model resolver [#172]

---

## [0.5.15](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.15) - 2020-07-19

- Implements -Handle double __ in snake to camel function- [#175] by @skrawcz 

Thanks to @skrawcz 

---

## [0.5.14](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.14) - 2020-07-14

- Add option to convert all con* annotations to Field constraint options [#173] by @joshbode 

Thanks to @joshbode 

---

## [0.5.13](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.13) - 2020-06-30

- Fix incorrect optional type on the aliased field [#167]

---

## [0.5.12](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.12) - 2020-06-29

- Space and special characters in keys [#166]

---

## [0.5.11](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.11) - 2020-06-25

- support nested array [#165]

---

## [0.5.10](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.10) - 2020-06-20

- fix default [#161]

---

## [0.5.9](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.9) - 2020-06-19

- fix condition for typed_default [#155]

---

## [0.5.8](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.8) - 2020-06-17

- Support typed default value [#154]

---

## [0.5.7](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.7) - 2020-06-14

- add __str__ on Imports [#152]

---

## [0.5.6](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.6) - 2020-06-13

- support one_of [#147]

---

## [0.5.5](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.5) - 2020-06-12

- Support enum in array [#149]
- Fix multiple sub-type in List [#150]

---

## [0.5.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.4) - 2020-06-11

- fix `anyof` in array fields [#146]

---

## [0.5.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.3) - 2020-06-11

- dynamic field serialization [#141] by @FlorianLudwig

Thanks to @FlorianLudwig

---

## [0.5.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.2) - 2020-06-05

- support decimal [#137]

---

## [0.5.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.1) - 2020-06-05

- support empty dict on items [#135]
- support hostname [#136]

---

## [0.5.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.5.0) - 2020-06-02

- Breaking Change: Fix invalid python name [#132]
The fixes change the class name of non-Upper Camel to Upper Camel

---

## [0.4.11](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.11) - 2020-05-19

- Fix args of `conint` and `confloat` [#128]

---

## [0.4.10](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.10) - 2020-05-06

- Fix parse errors when property pattern is explicit. [#125] by @mgonzalezperna

Thanks to @mgonzalezperna

---

## [0.4.9](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.9) - 2020-04-22

- Fixed problems, if a name starts with a underscore [#120] by @julian-r

Thanks to @julian-r  

---

## [0.4.8](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.8) - 2020-04-18

- update pydantic version [#119]
- create `generate` function [#118]

---

## [0.4.7](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.7) - 2020-04-14

- Support json and yaml [#115]

---

## [0.4.6](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.6) - 2020-04-06

- fix as a statement on import [#114]

---

## [0.4.5](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.5) - 2020-04-05

- support as on import statement [#112]
- support empty array on JsonSchema [#113]

---

## [0.4.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.4) - 2020-04-01

- handle edge-case where models map to the __init__.py level of a module [#110] by @joshbode
Thanks to @joshbode

---

## [0.4.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.3) - 2020-03-31

- import version to get full 100% coverage [#109] by @joshbode
Thanks to @joshbode

---

## [0.4.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.2) - 2020-03-30

- Correct population of modular structure [#96] by @joshbode

Thanks to @joshbode

---

## [0.4.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.1) - 2020-03-23

- support empty object as any root object [#106]
- support reserved words as a field [#107]
- change validation to optional [#108]

---

## [0.4.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.4.0) - 2020-03-16

- improve jsonschema [#104]
- support array of type attribute on jsonschema [#103]
- change url-validator [#100]
- support `Field` [#99]
- correct string-normalization to skip-string-normalization #98 by @joshbode
- move lint configuration (black, isort, mypy) to configuration files [#97]  by @joshbode

Thanks to @joshbode

---

## [0.3.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.3.3) - 2020-02-26

- Change numeric constraints to correct mapping [#94] by @surajbarkale

Thanks to @surajbarkale

---

## [0.3.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.3.2) - 2020-02-05

add support for arrays of simple primities to anyOf [#92] by @joshbode

Thanks to @joshbode

---

## [0.3.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.3.1) - 2020-02-03

- Move logic for anyOf/allOf to higher priority [#91] by @joshbode

Thanks to @joshbode

---

## [0.3.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.3.0) - 2020-01-09

- Support JsonSchema [#89]
- Support Remote reference [#87]

---

## [0.2.16](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.16) - 2019-12-13

- Fix types requiring imports in alloff, conint, confloat [#84] by @krezac 

Thanks to @krezac

---

## [0.2.15](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.15) - 2019-12-04

- Support `additionalProperties`  as `Extra.allow` [#83] 

---

## [0.2.14](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.14) - 2019-11-25

- add support for pyproject.toml if present ［#80］by @joshbode
Thanks to @joshbode

---

## [0.2.13](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.13) - 2019-11-22

- Support for Primitve union properties [#79] by @joshbode

Thanks to @joshbode

---

## [0.2.12](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.12) - 2019-11-04

- update dependency versions [#78]

---

## [0.2.11](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.11) - 2019-10-18

- add version option [#75]

---

## [0.2.10](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.10) - 2019-10-18

- support enum alias [#74]

---

## [0.2.9](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.9) - 2019-10-17

- Fix some edge-cases with module references [#73] by @joshbode 

---

## [0.2.8](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.8) - 2019-10-16

- Add support for custom templates and extra template data [#71] by @joshbode
Thanks to @joshbode

---

## [0.2.7](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.7) - 2019-10-15

- add support for object arrays [#70] by @joshbode 
Thanks to @joshbode 

---

## [0.2.6](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.6) - 2019-10-10

- Add missing imports for `constr` and `Dict[str, Any]` [#69] by @joshbode

---

## [0.2.5](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.5) - 2019-10-09

- add scripts for a test, lint, and format [#68] by @koxudaxi 
- Add support for "modular" schemas [#66] by @joshbode 
- Fix Import path for UUID [#66] by @joshbode 
Thanks to @joshbode

---

## [0.2.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.4) - 2019-09-26

add debug mode as `--debug`

---

## [0.2.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.3) - 2019-09-13

- fix to add `List` on an import statement
- support normalize enum fields 

---

## [0.2.2](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.2) - 2019-09-13

- fix classes order

---

## [0.2.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.1) - 2019-09-13

- support anyOf
- support allOf

---

## [0.2.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.2.0) - 2019-09-05

- support enum
- support forward refs
- support stdin as input file
- target python version
- support duplicate models
- improve to parse array object

---

## [0.1.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.1.0) - 2019-08-06

## add features
- custom base model
- null
- create import statements
- string pattern 
- Minimum
- Maximum
- exclusiveMinimum 
- exclusiveMaximum
- multipleOf

---

## [0.0.6](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.0.6) - 2019-07-31

## add supported formats
- email
- uri
- ipv4
- ipv6
- time

---

## [0.0.5](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.0.5) - 2019-07-26

- refactor a parser
- support custom_root_type
- add unittest

---

## [0.0.4](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.0.4) - 2019-07-23

- convert plurals name to singular name for array items

---

## [0.0.3](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.0.3) - 2019-07-23

- fix parsing objects

---

## [0.0.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.0.1) - 2019-07-23

- Support generate pydantic models from openapi models

Thanks to @joshbode


---

