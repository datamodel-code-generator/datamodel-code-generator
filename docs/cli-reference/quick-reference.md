# 🔍 Quick Reference

All CLI options in one page for easy **Ctrl+F** searching.

👆 Click any option to see detailed documentation with examples.

---

```text
datamodel-codegen [OPTIONS]
```

## 📂 All Options by Category

### 📁 Base Options

| Option | Description |
|--------|-------------|
| [`--emit-model-metadata`](base-options.md#emit-model-metadata) | Write a separate JSON map from source schema references to the final generated m... |
| [`--encoding`](base-options.md#encoding) | Specify character encoding for input and output files. |
| [`--external-ref-mapping`](base-options.md#external-ref-mapping) | Map external `$ref` files to Python packages. |
| [`--http-backend`](base-options.md#http-backend) | Select the HTTP client backend for remote schemas. |
| [`--input`](base-options.md#input) | Specify the input schema file path. |
| [`--input-file-type`](base-options.md#input-file-type) | Specify the input file type for code generation. |
| [`--input-model`](base-options.md#input-model) | Import a Python type or dict schema from a module or Python file. |
| [`--input-model-ref-strategy`](base-options.md#input-model-ref-strategy) | Strategy for referenced types when using --input-model. |
| [`--output`](base-options.md#output) | Specify the destination path for generated Python code. |
| [`--preset`](base-options.md#preset) | Apply an immutable built-in option preset. |
| [`--schema-version`](base-options.md#schema-version) | Schema version to use for parsing. |
| [`--schema-version-mode`](base-options.md#schema-version-mode) | Schema version validation mode. |
| [`--url`](base-options.md#url) | Fetch a schema from a URL with custom HTTP headers. |

### 🔧 Typing Customization

| Option | Description |
|--------|-------------|
| [`--allof-class-hierarchy`](typing-customization.md#allof-class-hierarchy) | Controls how allOf schemas are represented in the generated class hierarchy. `--... |
| [`--allof-merge-mode`](typing-customization.md#allof-merge-mode) | Merge constraints from root model references in allOf schemas. |
| [`--disable-future-imports`](typing-customization.md#disable-future-imports) | Prevent automatic addition of __future__ imports in generated code. |
| [`--enum-field-as-literal`](typing-customization.md#enum-field-as-literal) | Convert all enum fields to Literal types instead of Enum classes. |
| [`--enum-field-as-literal-map`](typing-customization.md#enum-field-as-literal-map) | Override enum/literal generation per-field via JSON mapping. |
| [`--ignore-enum-constraints`](typing-customization.md#ignore-enum-constraints) | Ignore enum constraints and use base string type instead of Enum classes. |
| [`--import-overrides`](typing-customization.md#import-overrides) | Override modules for generated imports by symbol name. |
| [`--no-use-closed-typed-dict`](typing-customization.md#no-use-closed-typed-dict) | Disable PEP 728 TypedDict closed/extra_items generation. |
| [`--no-use-specialized-enum`](typing-customization.md#no-use-specialized-enum) | Disable specialized Enum classes for Python 3.11+ code generation. |
| [`--no-use-standard-collections`](typing-customization.md#no-use-standard-collections) | Use typing.Dict/List instead of built-in dict/list for container types. |
| [`--no-use-union-operator`](typing-customization.md#no-use-union-operator) | Use Union[X, Y] / Optional[X] instead of X | Y union operator. |
| [`--output-date-class`](typing-customization.md#output-date-class) | Specify date class type for date schema fields. |
| [`--output-datetime-class`](typing-customization.md#output-datetime-class) | Specify datetime class type for date-time schema fields. |
| [`--strict-types`](typing-customization.md#strict-types) | Enable strict type validation for specified Python types. |
| [`--type-mappings`](typing-customization.md#type-mappings) | Override default type mappings for schema formats. |
| [`--type-overrides`](typing-customization.md#type-overrides) | Replace schema model types with custom Python types via JSON mapping. |
| [`--use-annotated`](typing-customization.md#use-annotated) | Use typing.Annotated for Field() with constraints. |
| [`--use-closed-typed-dict`](typing-customization.md#use-closed-typed-dict) | Generate TypedDict with PEP 728 closed/extra_items (default: enabled). |
| [`--use-decimal-for-multiple-of`](typing-customization.md#use-decimal-for-multiple-of) | Generate Decimal types for fields with multipleOf constraint. |
| [`--use-generic-container-types`](typing-customization.md#use-generic-container-types) | Use generic container types (Sequence, Mapping) for type hinting. |
| [`--use-non-positive-negative-number-constrained-types`](typing-customization.md#use-non-positive-negative-number-constrained-types) | Use NonPositive/NonNegative types for number constraints. |
| [`--use-object-type`](typing-customization.md#use-object-type) | Use object instead of Any for unspecified object and array values. |
| [`--use-pendulum`](typing-customization.md#use-pendulum) | Use pendulum types for date, time, and duration fields. |
| [`--use-root-model-type-alias`](typing-customization.md#use-root-model-type-alias) | Generate RootModel as type alias format for better mypy support. |
| [`--use-specialized-enum`](typing-customization.md#use-specialized-enum) | Generate StrEnum/IntEnum for string/integer enums (Python 3.11+). |
| [`--use-standard-collections`](typing-customization.md#use-standard-collections) | Use built-in dict/list instead of typing.Dict/List. |
| [`--use-standard-primitive-types`](typing-customization.md#use-standard-primitive-types) | Use Python standard library types for string formats instead of str. |
| [`--use-total-false-for-typed-dict`](typing-customization.md#use-total-false-for-typed-dict) | Generate TypedDict declarations with `total=False`. |
| [`--use-tuple-for-fixed-items`](typing-customization.md#use-tuple-for-fixed-items) | Generate tuple types for arrays with items array syntax. |
| [`--use-tuple-for-fixed-length-arrays`](typing-customization.md#use-tuple-for-fixed-length-arrays) | Generate tuple types for homogeneous fixed-length arrays. |
| [`--use-type-alias`](typing-customization.md#use-type-alias) | Use TypeAlias instead of root models for type definitions (experimental). |
| [`--use-type-alias-type`](typing-customization.md#use-type-alias-type) | Use runtime TypeAliasType objects for aliases before Python 3.12 (experimental). |
| [`--use-union-operator`](typing-customization.md#use-union-operator) | Use | operator for Union types (PEP 604). |
| [`--use-unique-items-as-set`](typing-customization.md#use-unique-items-as-set) | Generate set types for arrays with uniqueItems constraint. |

### 🏷️ Field Customization

| Option | Description |
|--------|-------------|
| [`--aliases`](field-customization.md#aliases) | Apply custom field and class name aliases via inline JSON or a JSON file path. |
| [`--capitalize-enum-members`](field-customization.md#capitalize-enum-members) | Capitalize enum member names to UPPER_CASE format. |
| [`--default-values`](field-customization.md#default-values) | Override field default values via inline JSON or a JSON file path. |
| [`--deserialize-default-values`](field-customization.md#deserialize-default-values) | Deserialize selected direct scalar field defaults into their generated Python ty... |
| [`--empty-enum-field-name`](field-customization.md#empty-enum-field-name) | Name for empty string enum field values. |
| [`--extra-fields`](field-customization.md#extra-fields) | Configure how generated models handle extra fields not defined in schema. |
| [`--field-constraints`](field-customization.md#field-constraints) | Generate Field() with validation constraints from schema. |
| [`--field-extra-keys`](field-customization.md#field-extra-keys) | Include specific extra keys in Field() definitions. |
| [`--field-extra-keys-without-x-prefix`](field-customization.md#field-extra-keys-without-x-prefix) | Include schema extension keys in Field() without requiring 'x-' prefix. |
| [`--field-include-all-keys`](field-customization.md#field-include-all-keys) | Include all schema keys in Field() json_schema_extra. |
| [`--field-type-collision-strategy`](field-customization.md#field-type-collision-strategy) | Rename type class instead of field when names collide (Pydantic v2 only). |
| [`--infer-union-variant-names`](field-customization.md#infer-union-variant-names) | Infer names for inline oneOf/anyOf object variants from literal fields. |
| [`--no-alias`](field-customization.md#no-alias) | Disable Field alias generation for non-Python-safe property names. |
| [`--no-deserialize-default-values`](field-customization.md#no-deserialize-default-values) | Keep serialized schema defaults instead of deserializing selected types. |
| [`--original-field-name-delimiter`](field-customization.md#original-field-name-delimiter) | Specify delimiter for original field names when using snake-case conversion. |
| [`--remove-special-field-name-prefix`](field-customization.md#remove-special-field-name-prefix) | Remove the special prefix from field names. |
| [`--serialization-aliases`](field-customization.md#serialization-aliases) | Apply custom Pydantic v2 serialization aliases via inline JSON or a JSON file pa... |
| [`--set-default-enum-member`](field-customization.md#set-default-enum-member) | Use the legacy flag for deserializing enum defaults. |
| [`--snake-case-field`](field-customization.md#snake-case-field) | Convert field names to snake_case format. |
| [`--special-field-name-prefix`](field-customization.md#special-field-name-prefix) | Prefix to add to special field names (like reserved keywords). |
| [`--use-attribute-docstrings`](field-customization.md#use-attribute-docstrings) | Generate field descriptions as attribute docstrings instead of Field description... |
| [`--use-enum-values-in-discriminator`](field-customization.md#use-enum-values-in-discriminator) | Use enum values in discriminator mappings for union types. |
| [`--use-field-description`](field-customization.md#use-field-description) | Include schema descriptions as Field docstrings. |
| [`--use-field-description-example`](field-customization.md#use-field-description-example) | Add field examples to docstrings. |
| [`--use-inline-field-description`](field-customization.md#use-inline-field-description) | Add field descriptions as inline comments. |
| [`--use-schema-description`](field-customization.md#use-schema-description) | Use schema description as class docstring. |
| [`--use-serialization-alias`](field-customization.md#use-serialization-alias) | Use serialization_alias instead of alias for field aliasing (Pydantic v2 only). |
| [`--use-single-line-docstring`](field-customization.md#use-single-line-docstring) | Emit short docstrings on a single line. |
| [`--use-title-as-name`](field-customization.md#use-title-as-name) | Use schema title as the generated class name. |

### 🏗️ Model Customization

| Option | Description |
|--------|-------------|
| [`--alias-generator`](model-customization.md#alias-generator) | Use a Pydantic v2 alias generator in model_config. |
| [`--allow-extra-fields`](model-customization.md#allow-extra-fields) | Allow extra fields in generated Pydantic models (extra='allow'). |
| [`--allow-leading-underscore-class-name`](model-customization.md#allow-leading-underscore-class-name) | Allow an explicitly specified root class name to start with an underscore. |
| [`--allow-population-by-field-name`](model-customization.md#allow-population-by-field-name) | Allow Pydantic model population by field name (not just alias). |
| [`--base-class`](model-customization.md#base-class) | Specify a custom base class for generated models. |
| [`--base-class-map`](model-customization.md#base-class-map) | Specify different base classes for specific models via JSON mapping. |
| [`--class-name`](model-customization.md#class-name) | Override the auto-generated class name with a custom name. |
| [`--class-name-affix-scope`](model-customization.md#class-name-affix-scope) | Control which classes receive the prefix/suffix. |
| [`--class-name-prefix`](model-customization.md#class-name-prefix) | Add a prefix to all generated class names. |
| [`--class-name-suffix`](model-customization.md#class-name-suffix) | Add a suffix to all generated class names. |
| [`--collapse-reuse-models`](model-customization.md#collapse-reuse-models) | Collapse duplicate models by replacing references instead of inheritance. |
| [`--collapse-root-models`](model-customization.md#collapse-root-models) | Inline root model definitions instead of creating separate wrapper classes. |
| [`--collapse-root-models-name-strategy`](model-customization.md#collapse-root-models-name-strategy) | Select which name to keep when collapsing root models with object references. |
| [`--dataclass-arguments`](model-customization.md#dataclass-arguments) | Customize dataclass decorator arguments via JSON dictionary. |
| [`--duplicate-name-suffix`](model-customization.md#duplicate-name-suffix) | Customize suffix for duplicate model names. |
| [`--enable-faux-immutability`](model-customization.md#enable-faux-immutability) | Enable faux immutability in Pydantic models (frozen=True). |
| [`--force-optional`](model-customization.md#force-optional) | Force all fields to be Optional regardless of required status. |
| [`--frozen-dataclasses`](model-customization.md#frozen-dataclasses) | Generate frozen dataclasses with optional keyword-only fields. |
| [`--keep-model-order`](model-customization.md#keep-model-order) | Keep generated model order deterministic while respecting dependency constraints... |
| [`--keyword-only`](model-customization.md#keyword-only) | Generate dataclasses with keyword-only fields (Python 3.10+). |
| [`--model-extra-keys`](model-customization.md#model-extra-keys) | Add model-level schema extensions to ConfigDict json_schema_extra. |
| [`--model-extra-keys-without-x-prefix`](model-customization.md#model-extra-keys-without-x-prefix) | Strip x- prefix from model-level schema extensions and add to ConfigDict json_sc... |
| [`--model-name-map`](model-customization.md#model-name-map) | Rename generated model classes from a JSON mapping. |
| [`--naming-strategy`](model-customization.md#naming-strategy) | Use parent-prefixed naming strategy for duplicate model names. |
| [`--output-model-type`](model-customization.md#output-model-type) | Select the output model type (Pydantic v2, Pydantic v2 dataclass, dataclasses, T... |
| [`--parent-scoped-naming`](model-customization.md#parent-scoped-naming) | Namespace models by their parent scope to avoid naming conflicts. |
| [`--reuse-model`](model-customization.md#reuse-model) | Reuse identical model definitions instead of generating duplicates. |
| [`--reuse-scope`](model-customization.md#reuse-scope) | Scope for model reuse detection (root or tree). |
| [`--skip-root-model`](model-customization.md#skip-root-model) | Skip generation of root model when schema contains nested definitions. |
| [`--strict-nullable`](model-customization.md#strict-nullable) | Treat default field as a non-nullable field. |
| [`--strip-default-none`](model-customization.md#strip-default-none) | Remove fields with None as default value from generated models. |
| [`--target-pydantic-version`](model-customization.md#target-pydantic-version) | Target Pydantic version for generated code compatibility. |
| [`--target-python-version`](model-customization.md#target-python-version) | Target Python version for generated code syntax and imports. |
| [`--union-mode`](model-customization.md#union-mode) | Union mode for combining anyOf/oneOf schemas (smart or left_to_right). |
| [`--use-default`](model-customization.md#use-default) | Use default values from schema in generated models. |
| [`--use-default-factory-for-optional-nested-models`](model-customization.md#use-default-factory-for-optional-nested-models) | Generate default_factory for optional nested model fields. |
| [`--use-default-kwarg`](model-customization.md#use-default-kwarg) | Use default= keyword argument instead of positional argument for fields with def... |
| [`--use-frozen-field`](model-customization.md#use-frozen-field) | Generate frozen (immutable) field definitions for readOnly properties. |
| [`--use-generic-base-class`](model-customization.md#use-generic-base-class) | Generate a shared base class with model configuration to avoid repetition (DRY). |
| [`--use-missing-sentinel`](model-customization.md#use-missing-sentinel) | Use Pydantic's MISSING sentinel for optional fields without defaults. |
| [`--use-one-literal-as-default`](model-customization.md#use-one-literal-as-default) | Use single literal value as default when enum has only one option. |
| [`--use-root-model-sequence-interface`](model-customization.md#use-root-model-sequence-interface) | Make non-null sequence-like Pydantic v2 RootModel classes implement collections.... |
| [`--use-serialize-as-any`](model-customization.md#use-serialize-as-any) | Wrap fields with subtypes in Pydantic's SerializeAsAny. |
| [`--use-subclass-enum`](model-customization.md#use-subclass-enum) | Generate typed Enum subclasses for enums with specific field types. |

### 🎨 Template Customization

| Option | Description |
|--------|-------------|
| [`--additional-imports`](template-customization.md#additional-imports) | Add custom imports to generated output files. |
| [`--class-decorators`](template-customization.md#class-decorators) | Add custom decorators to generated model classes. |
| [`--custom-file-header`](template-customization.md#custom-file-header) | Add custom header text to the generated file. |
| [`--custom-file-header-mode`](template-customization.md#custom-file-header-mode) | Choose how a custom file header combines with generated provenance. |
| [`--custom-file-header-path`](template-customization.md#custom-file-header-path) | Add custom header content from file to generated code. |
| [`--custom-formatters`](template-customization.md#custom-formatters) | Apply custom Python code formatters to generated output. |
| [`--custom-formatters-kwargs`](template-customization.md#custom-formatters-kwargs) | Pass custom arguments to custom formatters via inline JSON or a JSON file path. |
| [`--custom-template-dir`](template-customization.md#custom-template-dir) | Use custom Jinja2 templates for model generation. |
| [`--disable-appending-item-suffix`](template-customization.md#disable-appending-item-suffix) | Disable appending 'Item' suffix to array item types. |
| [`--disable-timestamp`](template-customization.md#disable-timestamp) | Disable timestamp in generated file header for reproducible output. |
| [`--enable-command-header`](template-customization.md#enable-command-header) | Include command-line options in file header for reproducibility. |
| [`--enable-generated-header-marker`](template-customization.md#enable-generated-header-marker) | Include the @generated marker in file header for generated-code tooling. |
| [`--enable-version-header`](template-customization.md#enable-version-header) | Include tool version information in file header. |
| [`--extra-template-data`](template-customization.md#extra-template-data) | Pass custom template variables via inline JSON or a JSON file path. |
| [`--formatters`](template-customization.md#formatters) | Specify code formatters to apply to generated output. |
| [`--generate-schema-validators`](template-customization.md#generate-schema-validators) | Generate experimental Pydantic v2 model validators for JSON Schema runtime rules... |
| [`--no-treat-dot-as-module`](template-customization.md#no-treat-dot-as-module) | Keep dots in schema names as underscores for flat output. |
| [`--no-use-type-checking-imports`](template-customization.md#no-use-type-checking-imports) | Keep generated model imports available at runtime when using Ruff fixes. |
| [`--schema-validator-base-class-name`](template-customization.md#schema-validator-base-class-name) | Set the generated shared Pydantic v2 schema runtime validator base class name. |
| [`--schema-validator-type`](template-customization.md#schema-validator-type) | Select the schema-derived runtime validator backend. |
| [`--strict-dotted-module-names`](template-customization.md#strict-dotted-module-names) | Require canonical Python identifiers when inferring dotted module paths. |
| [`--treat-dot-as-module`](template-customization.md#treat-dot-as-module) | Treat dots in schema names as module separators. |
| [`--use-double-quotes`](template-customization.md#use-double-quotes) | Use double quotes for string literals in generated code. |
| [`--use-exact-imports`](template-customization.md#use-exact-imports) | Import exact types instead of modules. |
| [`--use-type-checking-imports`](template-customization.md#use-type-checking-imports) | Allow Ruff to move typing-only imports into TYPE_CHECKING blocks. |
| [`--validators`](template-customization.md#validators) | Add custom field validators to generated Pydantic v2 models. |
| [`--wrap-string-literal`](template-customization.md#wrap-string-literal) | Wrap long string literals across multiple lines. |

### 📘 OpenAPI-only Options

| Option | Description |
|--------|-------------|
| [`--include-path-parameters`](openapi-only-options.md#include-path-parameters) | Include OpenAPI path parameters in generated parameter models. |
| [`--openapi-include-info-version`](openapi-only-options.md#openapi-include-info-version) | Emit OpenAPI info.version as a generated constant. |
| [`--openapi-include-paths`](openapi-only-options.md#openapi-include-paths) | Filter OpenAPI paths to include in model generation. |
| [`--openapi-scopes`](openapi-only-options.md#openapi-scopes) | Specify OpenAPI scopes to generate (schemas, paths, parameters). |
| [`--read-only-write-only-model-type`](openapi-only-options.md#read-only-write-only-model-type) | Generate separate request and response models for readOnly/writeOnly fields. |
| [`--use-operation-id-as-name`](openapi-only-options.md#use-operation-id-as-name) | Use OpenAPI operationId as the generated function/class name. |
| [`--use-status-code-in-response-name`](openapi-only-options.md#use-status-code-in-response-name) | Include HTTP status code in response model names. |
| [`--validation`](openapi-only-options.md#validation) | Enable validation constraints (deprecated, use --field-constraints). |

### 📋 GraphQL-only Options

| Option | Description |
|--------|-------------|
| [`--graphql-no-typename`](graphql-only-options.md#graphql-no-typename) | Exclude __typename field from generated GraphQL models. |

### ⚙️ General Options

| Option | Description |
|--------|-------------|
| [`--all-exports-collision-strategy`](general-options.md#all-exports-collision-strategy) | Handle name collisions when exporting recursive module hierarchies. |
| [`--all-exports-scope`](general-options.md#all-exports-scope) | Generate __all__ exports for child modules in __init__.py files. |
| [`--allow-private-network`](general-options.md#allow-private-network) | Allow HTTP requests to private network schema endpoints. |
| [`--allow-remote-refs`](general-options.md#allow-remote-refs) | Enable fetching of `$ref` targets over HTTP/HTTPS. |
| [`--check`](general-options.md#check) | Verify generated code matches existing output without modifying files. |
| [`--diff-against`](general-options.md#diff-against) | Compare generated code from a baseline input with the current schema without wri... |
| [`--disable-warnings`](general-options.md#disable-warnings) | Suppress warning messages during code generation. |
| [`--fail-on-multi-module-stdout`](general-options.md#fail-on-multi-module-stdout) | Fail instead of concatenating multiple modules in text stdout. |
| [`--generate-cli-command`](general-options.md#generate-cli-command) | Generate CLI command from pyproject.toml configuration. |
| [`--generate-pyproject-config`](general-options.md#generate-pyproject-config) | Generate pyproject.toml configuration from CLI arguments. |
| [`--http-headers`](general-options.md#http-headers) | Fetch a schema from a URL with custom HTTP headers. |
| [`--http-ignore-tls`](general-options.md#http-ignore-tls) | Disable TLS certificate verification for HTTPS requests. |
| [`--http-local-ref-path`](general-options.md#http-local-ref-path) | Resolve HTTP references from local schema files. |
| [`--http-query-parameters`](general-options.md#http-query-parameters) | Add query parameters to HTTP requests for remote schemas. |
| [`--http-timeout`](general-options.md#http-timeout) | Set timeout for HTTP requests to remote hosts. |
| [`--ignore-pyproject`](general-options.md#ignore-pyproject) | Ignore pyproject.toml configuration file. |
| [`--locked`](general-options.md#locked) | Require an existing remote lock and validate each fetched resource against it (e... |
| [`--lockfile`](general-options.md#lockfile) | Select the remote reference integrity lock file (experimental). |
| [`--module-split-mode`](general-options.md#module-split-mode) | Split generated models into separate files, one per model class. |
| [`--shared-module-name`](general-options.md#shared-module-name) | Customize the name of the shared module for deduplicated models. |
| [`--strict-refs`](general-options.md#strict-refs) | Treat unresolved local `$ref` JSON pointers as errors. |
| [`--update-lock`](general-options.md#update-lock) | Create or atomically update the selected remote lock after generation (experimen... |
| [`--watch`](general-options.md#watch) | Watch input file(s) for changes and regenerate output automatically. |
| [`--watch-delay`](general-options.md#watch-delay) | Set debounce delay in seconds for watch mode. |

### 📝 Utility Options

| Option | Description |
|--------|-------------|
| [`--all-jobs`](utility-options.md#all-jobs) | Run every named generation job from pyproject.toml (experimental) |
| [`--debug`](utility-options.md#debug) | Show debug messages during code generation |
| [`--dependency-format`](utility-options.md#dependency-format) | Print the generation target's dependencies for uv or a requirements file |
| [`--diagnostics-json`](utility-options.md#diagnostics-json) | Write the generation target's diagnostics as JSON |
| [`--generate-prompt`](utility-options.md#generate-prompt) | Generate a prompt for consulting LLMs about CLI options |
| [`--generate-server`](utility-options.md#generate-server) | Generate a FastAPI server package with the models (experimental) |
| [`--help`](utility-options.md#help) | Show help message and exit |
| [`--install-skill`](utility-options.md#install-skill) | Install the bundled Agent Skill (experimental) |
| [`--job`](utility-options.md#job) | Run a named generation job from pyproject.toml (experimental) |
| [`--list-deprecations`](utility-options.md#list-deprecations) | List registered deprecations and scheduled breaking changes |
| [`--list-experimental`](utility-options.md#list-experimental) | List registered experimental features |
| [`--no-color`](utility-options.md#no-color) | Disable colorized output |
| [`--output-format`](utility-options.md#output-format) | Choose the command output format |
| [`--output-format-json-schema`](utility-options.md#output-format-json-schema) | Output JSON Schema for structured command output or JSON configuration |
| [`--overwrite-skill`](utility-options.md#overwrite-skill) | Replace an existing Agent Skill installation |
| [`--profile`](utility-options.md#profile) | Use a named profile from pyproject.toml |
| [`--skill-scope`](utility-options.md#skill-scope) | Choose an Agent Skill installation scope |
| [`--target-config`](utility-options.md#target-config) | Read the generation target's settings from a TOML file |
| [`--target-output`](utility-options.md#target-output) | Override the generation target's output directory |
| [`--version`](utility-options.md#version) | Show program version and exit |

---

## 🔤 Alphabetical Index

All options sorted alphabetically:

- [`--additional-imports`](template-customization.md#additional-imports) - Add custom imports to generated output files.
- [`--alias-generator`](model-customization.md#alias-generator) - Use a Pydantic v2 alias generator in model_config.
- [`--aliases`](field-customization.md#aliases) - Apply custom field and class name aliases via inline JSON or...
- [`--all-exports-collision-strategy`](general-options.md#all-exports-collision-strategy) - Handle name collisions when exporting recursive module hiera...
- [`--all-exports-scope`](general-options.md#all-exports-scope) - Generate __all__ exports for child modules in __init__.py fi...
- [`--all-jobs`](utility-options.md#all-jobs) - Run every named generation job from pyproject.toml (experimental)
- [`--allof-class-hierarchy`](typing-customization.md#allof-class-hierarchy) - Controls how allOf schemas are represented in the generated ...
- [`--allof-merge-mode`](typing-customization.md#allof-merge-mode) - Merge constraints from root model references in allOf schema...
- [`--allow-extra-fields`](model-customization.md#allow-extra-fields) - Allow extra fields in generated Pydantic models (extra='allo...
- [`--allow-leading-underscore-class-name`](model-customization.md#allow-leading-underscore-class-name) - Allow an explicitly specified root class name to start with ...
- [`--allow-population-by-field-name`](model-customization.md#allow-population-by-field-name) - Allow Pydantic model population by field name (not just alia...
- [`--allow-private-network`](general-options.md#allow-private-network) - Allow HTTP requests to private network schema endpoints.
- [`--allow-remote-refs`](general-options.md#allow-remote-refs) - Enable fetching of `$ref` targets over HTTP/HTTPS.
- [`--base-class`](model-customization.md#base-class) - Specify a custom base class for generated models.
- [`--base-class-map`](model-customization.md#base-class-map) - Specify different base classes for specific models via JSON ...
- [`--capitalize-enum-members`](field-customization.md#capitalize-enum-members) - Capitalize enum member names to UPPER_CASE format.
- [`--check`](general-options.md#check) - Verify generated code matches existing output without modify...
- [`--class-decorators`](template-customization.md#class-decorators) - Add custom decorators to generated model classes.
- [`--class-name`](model-customization.md#class-name) - Override the auto-generated class name with a custom name.
- [`--class-name-affix-scope`](model-customization.md#class-name-affix-scope) - Control which classes receive the prefix/suffix.
- [`--class-name-prefix`](model-customization.md#class-name-prefix) - Add a prefix to all generated class names.
- [`--class-name-suffix`](model-customization.md#class-name-suffix) - Add a suffix to all generated class names.
- [`--collapse-reuse-models`](model-customization.md#collapse-reuse-models) - Collapse duplicate models by replacing references instead of...
- [`--collapse-root-models`](model-customization.md#collapse-root-models) - Inline root model definitions instead of creating separate w...
- [`--collapse-root-models-name-strategy`](model-customization.md#collapse-root-models-name-strategy) - Select which name to keep when collapsing root models with o...
- [`--custom-file-header`](template-customization.md#custom-file-header) - Add custom header text to the generated file.
- [`--custom-file-header-mode`](template-customization.md#custom-file-header-mode) - Choose how a custom file header combines with generated prov...
- [`--custom-file-header-path`](template-customization.md#custom-file-header-path) - Add custom header content from file to generated code.
- [`--custom-formatters`](template-customization.md#custom-formatters) - Apply custom Python code formatters to generated output.
- [`--custom-formatters-kwargs`](template-customization.md#custom-formatters-kwargs) - Pass custom arguments to custom formatters via inline JSON o...
- [`--custom-template-dir`](template-customization.md#custom-template-dir) - Use custom Jinja2 templates for model generation.
- [`--dataclass-arguments`](model-customization.md#dataclass-arguments) - Customize dataclass decorator arguments via JSON dictionary.
- [`--debug`](utility-options.md#debug) - Show debug messages during code generation
- [`--default-values`](field-customization.md#default-values) - Override field default values via inline JSON or a JSON file...
- [`--dependency-format`](utility-options.md#dependency-format) - Print the generation target's dependencies for uv or a requirements file
- [`--deserialize-default-values`](field-customization.md#deserialize-default-values) - Deserialize selected direct scalar field defaults into their...
- [`--diagnostics-json`](utility-options.md#diagnostics-json) - Write the generation target's diagnostics as JSON
- [`--diff-against`](general-options.md#diff-against) - Compare generated code from a baseline input with the curren...
- [`--disable-appending-item-suffix`](template-customization.md#disable-appending-item-suffix) - Disable appending 'Item' suffix to array item types.
- [`--disable-future-imports`](typing-customization.md#disable-future-imports) - Prevent automatic addition of __future__ imports in generate...
- [`--disable-timestamp`](template-customization.md#disable-timestamp) - Disable timestamp in generated file header for reproducible ...
- [`--disable-warnings`](general-options.md#disable-warnings) - Suppress warning messages during code generation.
- [`--duplicate-name-suffix`](model-customization.md#duplicate-name-suffix) - Customize suffix for duplicate model names.
- [`--emit-model-metadata`](base-options.md#emit-model-metadata) - Write a separate JSON map from source schema references to t...
- [`--empty-enum-field-name`](field-customization.md#empty-enum-field-name) - Name for empty string enum field values.
- [`--enable-command-header`](template-customization.md#enable-command-header) - Include command-line options in file header for reproducibil...
- [`--enable-faux-immutability`](model-customization.md#enable-faux-immutability) - Enable faux immutability in Pydantic models (frozen=True).
- [`--enable-generated-header-marker`](template-customization.md#enable-generated-header-marker) - Include the @generated marker in file header for generated-c...
- [`--enable-version-header`](template-customization.md#enable-version-header) - Include tool version information in file header.
- [`--encoding`](base-options.md#encoding) - Specify character encoding for input and output files.
- [`--enum-field-as-literal`](typing-customization.md#enum-field-as-literal) - Convert all enum fields to Literal types instead of Enum cla...
- [`--enum-field-as-literal-map`](typing-customization.md#enum-field-as-literal-map) - Override enum/literal generation per-field via JSON mapping.
- [`--external-ref-mapping`](base-options.md#external-ref-mapping) - Map external `$ref` files to Python packages.
- [`--extra-fields`](field-customization.md#extra-fields) - Configure how generated models handle extra fields not defin...
- [`--extra-template-data`](template-customization.md#extra-template-data) - Pass custom template variables via inline JSON or a JSON fil...
- [`--fail-on-multi-module-stdout`](general-options.md#fail-on-multi-module-stdout) - Fail instead of concatenating multiple modules in text stdou...
- [`--field-constraints`](field-customization.md#field-constraints) - Generate Field() with validation constraints from schema.
- [`--field-extra-keys`](field-customization.md#field-extra-keys) - Include specific extra keys in Field() definitions.
- [`--field-extra-keys-without-x-prefix`](field-customization.md#field-extra-keys-without-x-prefix) - Include schema extension keys in Field() without requiring '...
- [`--field-include-all-keys`](field-customization.md#field-include-all-keys) - Include all schema keys in Field() json_schema_extra.
- [`--field-type-collision-strategy`](field-customization.md#field-type-collision-strategy) - Rename type class instead of field when names collide (Pydan...
- [`--force-optional`](model-customization.md#force-optional) - Force all fields to be Optional regardless of required statu...
- [`--formatters`](template-customization.md#formatters) - Specify code formatters to apply to generated output.
- [`--frozen-dataclasses`](model-customization.md#frozen-dataclasses) - Generate frozen dataclasses with optional keyword-only field...
- [`--generate-cli-command`](general-options.md#generate-cli-command) - Generate CLI command from pyproject.toml configuration.
- [`--generate-prompt`](utility-options.md#generate-prompt) - Generate a prompt for consulting LLMs about CLI options
- [`--generate-pyproject-config`](general-options.md#generate-pyproject-config) - Generate pyproject.toml configuration from CLI arguments.
- [`--generate-schema-validators`](template-customization.md#generate-schema-validators) - Generate experimental Pydantic v2 model validators for JSON ...
- [`--generate-server`](utility-options.md#generate-server) - Generate a FastAPI server package with the models (experimental)
- [`--graphql-no-typename`](graphql-only-options.md#graphql-no-typename) - Exclude __typename field from generated GraphQL models.
- [`--help`](utility-options.md#help) - Show help message and exit
- [`--http-backend`](base-options.md#http-backend) - Select the HTTP client backend for remote schemas.
- [`--http-headers`](general-options.md#http-headers) - Fetch a schema from a URL with custom HTTP headers.
- [`--http-ignore-tls`](general-options.md#http-ignore-tls) - Disable TLS certificate verification for HTTPS requests.
- [`--http-local-ref-path`](general-options.md#http-local-ref-path) - Resolve HTTP references from local schema files.
- [`--http-query-parameters`](general-options.md#http-query-parameters) - Add query parameters to HTTP requests for remote schemas.
- [`--http-timeout`](general-options.md#http-timeout) - Set timeout for HTTP requests to remote hosts.
- [`--ignore-enum-constraints`](typing-customization.md#ignore-enum-constraints) - Ignore enum constraints and use base string type instead of ...
- [`--ignore-pyproject`](general-options.md#ignore-pyproject) - Ignore pyproject.toml configuration file.
- [`--import-overrides`](typing-customization.md#import-overrides) - Override modules for generated imports by symbol name.
- [`--include-path-parameters`](openapi-only-options.md#include-path-parameters) - Include OpenAPI path parameters in generated parameter model...
- [`--infer-union-variant-names`](field-customization.md#infer-union-variant-names) - Infer names for inline oneOf/anyOf object variants from lite...
- [`--input`](base-options.md#input) - Specify the input schema file path.
- [`--input-file-type`](base-options.md#input-file-type) - Specify the input file type for code generation.
- [`--input-model`](base-options.md#input-model) - Import a Python type or dict schema from a module or Python ...
- [`--input-model-ref-strategy`](base-options.md#input-model-ref-strategy) - Strategy for referenced types when using --input-model.
- [`--install-skill`](utility-options.md#install-skill) - Install the bundled Agent Skill (experimental)
- [`--job`](utility-options.md#job) - Run a named generation job from pyproject.toml (experimental)
- [`--keep-model-order`](model-customization.md#keep-model-order) - Keep generated model order deterministic while respecting de...
- [`--keyword-only`](model-customization.md#keyword-only) - Generate dataclasses with keyword-only fields (Python 3.10+)...
- [`--list-deprecations`](utility-options.md#list-deprecations) - List registered deprecations and scheduled breaking changes
- [`--list-experimental`](utility-options.md#list-experimental) - List registered experimental features
- [`--locked`](general-options.md#locked) - Require an existing remote lock and validate each fetched re...
- [`--lockfile`](general-options.md#lockfile) - Select the remote reference integrity lock file (experimenta...
- [`--model-extra-keys`](model-customization.md#model-extra-keys) - Add model-level schema extensions to ConfigDict json_schema_...
- [`--model-extra-keys-without-x-prefix`](model-customization.md#model-extra-keys-without-x-prefix) - Strip x- prefix from model-level schema extensions and add t...
- [`--model-name-map`](model-customization.md#model-name-map) - Rename generated model classes from a JSON mapping.
- [`--module-split-mode`](general-options.md#module-split-mode) - Split generated models into separate files, one per model cl...
- [`--naming-strategy`](model-customization.md#naming-strategy) - Use parent-prefixed naming strategy for duplicate model name...
- [`--no-alias`](field-customization.md#no-alias) - Disable Field alias generation for non-Python-safe property ...
- [`--no-color`](utility-options.md#no-color) - Disable colorized output
- [`--no-deserialize-default-values`](field-customization.md#no-deserialize-default-values) - Keep serialized schema defaults instead of deserializing sel...
- [`--no-treat-dot-as-module`](template-customization.md#no-treat-dot-as-module) - Keep dots in schema names as underscores for flat output.
- [`--no-use-closed-typed-dict`](typing-customization.md#no-use-closed-typed-dict) - Disable PEP 728 TypedDict closed/extra_items generation.
- [`--no-use-specialized-enum`](typing-customization.md#no-use-specialized-enum) - Disable specialized Enum classes for Python 3.11+ code gener...
- [`--no-use-standard-collections`](typing-customization.md#no-use-standard-collections) - Use typing.Dict/List instead of built-in dict/list for conta...
- [`--no-use-type-checking-imports`](template-customization.md#no-use-type-checking-imports) - Keep generated model imports available at runtime when using...
- [`--no-use-union-operator`](typing-customization.md#no-use-union-operator) - Use Union[X, Y] / Optional[X] instead of X | Y union operato...
- [`--openapi-include-info-version`](openapi-only-options.md#openapi-include-info-version) - Emit OpenAPI info.version as a generated constant.
- [`--openapi-include-paths`](openapi-only-options.md#openapi-include-paths) - Filter OpenAPI paths to include in model generation.
- [`--openapi-scopes`](openapi-only-options.md#openapi-scopes) - Specify OpenAPI scopes to generate (schemas, paths, paramete...
- [`--original-field-name-delimiter`](field-customization.md#original-field-name-delimiter) - Specify delimiter for original field names when using snake-...
- [`--output`](base-options.md#output) - Specify the destination path for generated Python code.
- [`--output-date-class`](typing-customization.md#output-date-class) - Specify date class type for date schema fields.
- [`--output-datetime-class`](typing-customization.md#output-datetime-class) - Specify datetime class type for date-time schema fields.
- [`--output-format`](utility-options.md#output-format) - Choose the command output format
- [`--output-format-json-schema`](utility-options.md#output-format-json-schema) - Output JSON Schema for structured command output or JSON configuration
- [`--output-model-type`](model-customization.md#output-model-type) - Select the output model type (Pydantic v2, Pydantic v2 datac...
- [`--overwrite-skill`](utility-options.md#overwrite-skill) - Replace an existing Agent Skill installation
- [`--parent-scoped-naming`](model-customization.md#parent-scoped-naming) - Namespace models by their parent scope to avoid naming confl...
- [`--preset`](base-options.md#preset) - Apply an immutable built-in option preset.
- [`--profile`](utility-options.md#profile) - Use a named profile from pyproject.toml
- [`--read-only-write-only-model-type`](openapi-only-options.md#read-only-write-only-model-type) - Generate separate request and response models for readOnly/w...
- [`--remove-special-field-name-prefix`](field-customization.md#remove-special-field-name-prefix) - Remove the special prefix from field names.
- [`--reuse-model`](model-customization.md#reuse-model) - Reuse identical model definitions instead of generating dupl...
- [`--reuse-scope`](model-customization.md#reuse-scope) - Scope for model reuse detection (root or tree).
- [`--schema-validator-base-class-name`](template-customization.md#schema-validator-base-class-name) - Set the generated shared Pydantic v2 schema runtime validato...
- [`--schema-validator-type`](template-customization.md#schema-validator-type) - Select the schema-derived runtime validator backend.
- [`--schema-version`](base-options.md#schema-version) - Schema version to use for parsing.
- [`--schema-version-mode`](base-options.md#schema-version-mode) - Schema version validation mode.
- [`--serialization-aliases`](field-customization.md#serialization-aliases) - Apply custom Pydantic v2 serialization aliases via inline JS...
- [`--set-default-enum-member`](field-customization.md#set-default-enum-member) - Use the legacy flag for deserializing enum defaults.
- [`--shared-module-name`](general-options.md#shared-module-name) - Customize the name of the shared module for deduplicated mod...
- [`--skill-scope`](utility-options.md#skill-scope) - Choose an Agent Skill installation scope
- [`--skip-root-model`](model-customization.md#skip-root-model) - Skip generation of root model when schema contains nested de...
- [`--snake-case-field`](field-customization.md#snake-case-field) - Convert field names to snake_case format.
- [`--special-field-name-prefix`](field-customization.md#special-field-name-prefix) - Prefix to add to special field names (like reserved keywords...
- [`--strict-dotted-module-names`](template-customization.md#strict-dotted-module-names) - Require canonical Python identifiers when inferring dotted m...
- [`--strict-nullable`](model-customization.md#strict-nullable) - Treat default field as a non-nullable field.
- [`--strict-refs`](general-options.md#strict-refs) - Treat unresolved local `$ref` JSON pointers as errors.
- [`--strict-types`](typing-customization.md#strict-types) - Enable strict type validation for specified Python types.
- [`--strip-default-none`](model-customization.md#strip-default-none) - Remove fields with None as default value from generated mode...
- [`--target-config`](utility-options.md#target-config) - Read the generation target's settings from a TOML file
- [`--target-output`](utility-options.md#target-output) - Override the generation target's output directory
- [`--target-pydantic-version`](model-customization.md#target-pydantic-version) - Target Pydantic version for generated code compatibility.
- [`--target-python-version`](model-customization.md#target-python-version) - Target Python version for generated code syntax and imports.
- [`--treat-dot-as-module`](template-customization.md#treat-dot-as-module) - Treat dots in schema names as module separators.
- [`--type-mappings`](typing-customization.md#type-mappings) - Override default type mappings for schema formats.
- [`--type-overrides`](typing-customization.md#type-overrides) - Replace schema model types with custom Python types via JSON...
- [`--union-mode`](model-customization.md#union-mode) - Union mode for combining anyOf/oneOf schemas (smart or left_...
- [`--update-lock`](general-options.md#update-lock) - Create or atomically update the selected remote lock after g...
- [`--url`](base-options.md#url) - Fetch a schema from a URL with custom HTTP headers.
- [`--use-annotated`](typing-customization.md#use-annotated) - Use typing.Annotated for Field() with constraints.
- [`--use-attribute-docstrings`](field-customization.md#use-attribute-docstrings) - Generate field descriptions as attribute docstrings instead ...
- [`--use-closed-typed-dict`](typing-customization.md#use-closed-typed-dict) - Generate TypedDict with PEP 728 closed/extra_items (default:...
- [`--use-decimal-for-multiple-of`](typing-customization.md#use-decimal-for-multiple-of) - Generate Decimal types for fields with multipleOf constraint...
- [`--use-default`](model-customization.md#use-default) - Use default values from schema in generated models.
- [`--use-default-factory-for-optional-nested-models`](model-customization.md#use-default-factory-for-optional-nested-models) - Generate default_factory for optional nested model fields.
- [`--use-default-kwarg`](model-customization.md#use-default-kwarg) - Use default= keyword argument instead of positional argument...
- [`--use-double-quotes`](template-customization.md#use-double-quotes) - Use double quotes for string literals in generated code.
- [`--use-enum-values-in-discriminator`](field-customization.md#use-enum-values-in-discriminator) - Use enum values in discriminator mappings for union types.
- [`--use-exact-imports`](template-customization.md#use-exact-imports) - Import exact types instead of modules.
- [`--use-field-description`](field-customization.md#use-field-description) - Include schema descriptions as Field docstrings.
- [`--use-field-description-example`](field-customization.md#use-field-description-example) - Add field examples to docstrings.
- [`--use-frozen-field`](model-customization.md#use-frozen-field) - Generate frozen (immutable) field definitions for readOnly p...
- [`--use-generic-base-class`](model-customization.md#use-generic-base-class) - Generate a shared base class with model configuration to avo...
- [`--use-generic-container-types`](typing-customization.md#use-generic-container-types) - Use generic container types (Sequence, Mapping) for type hin...
- [`--use-inline-field-description`](field-customization.md#use-inline-field-description) - Add field descriptions as inline comments.
- [`--use-missing-sentinel`](model-customization.md#use-missing-sentinel) - Use Pydantic's MISSING sentinel for optional fields without ...
- [`--use-non-positive-negative-number-constrained-types`](typing-customization.md#use-non-positive-negative-number-constrained-types) - Use NonPositive/NonNegative types for number constraints.
- [`--use-object-type`](typing-customization.md#use-object-type) - Use object instead of Any for unspecified object and array v...
- [`--use-one-literal-as-default`](model-customization.md#use-one-literal-as-default) - Use single literal value as default when enum has only one o...
- [`--use-operation-id-as-name`](openapi-only-options.md#use-operation-id-as-name) - Use OpenAPI operationId as the generated function/class name...
- [`--use-pendulum`](typing-customization.md#use-pendulum) - Use pendulum types for date, time, and duration fields.
- [`--use-root-model-sequence-interface`](model-customization.md#use-root-model-sequence-interface) - Make non-null sequence-like Pydantic v2 RootModel classes im...
- [`--use-root-model-type-alias`](typing-customization.md#use-root-model-type-alias) - Generate RootModel as type alias format for better mypy supp...
- [`--use-schema-description`](field-customization.md#use-schema-description) - Use schema description as class docstring.
- [`--use-serialization-alias`](field-customization.md#use-serialization-alias) - Use serialization_alias instead of alias for field aliasing ...
- [`--use-serialize-as-any`](model-customization.md#use-serialize-as-any) - Wrap fields with subtypes in Pydantic's SerializeAsAny.
- [`--use-single-line-docstring`](field-customization.md#use-single-line-docstring) - Emit short docstrings on a single line.
- [`--use-specialized-enum`](typing-customization.md#use-specialized-enum) - Generate StrEnum/IntEnum for string/integer enums (Python 3....
- [`--use-standard-collections`](typing-customization.md#use-standard-collections) - Use built-in dict/list instead of typing.Dict/List.
- [`--use-standard-primitive-types`](typing-customization.md#use-standard-primitive-types) - Use Python standard library types for string formats instead...
- [`--use-status-code-in-response-name`](openapi-only-options.md#use-status-code-in-response-name) - Include HTTP status code in response model names.
- [`--use-subclass-enum`](model-customization.md#use-subclass-enum) - Generate typed Enum subclasses for enums with specific field...
- [`--use-title-as-name`](field-customization.md#use-title-as-name) - Use schema title as the generated class name.
- [`--use-total-false-for-typed-dict`](typing-customization.md#use-total-false-for-typed-dict) - Generate TypedDict declarations with `total=False`.
- [`--use-tuple-for-fixed-items`](typing-customization.md#use-tuple-for-fixed-items) - Generate tuple types for arrays with items array syntax.
- [`--use-tuple-for-fixed-length-arrays`](typing-customization.md#use-tuple-for-fixed-length-arrays) - Generate tuple types for homogeneous fixed-length arrays.
- [`--use-type-alias`](typing-customization.md#use-type-alias) - Use TypeAlias instead of root models for type definitions (e...
- [`--use-type-alias-type`](typing-customization.md#use-type-alias-type) - Use runtime TypeAliasType objects for aliases before Python ...
- [`--use-type-checking-imports`](template-customization.md#use-type-checking-imports) - Allow Ruff to move typing-only imports into TYPE_CHECKING bl...
- [`--use-union-operator`](typing-customization.md#use-union-operator) - Use | operator for Union types (PEP 604).
- [`--use-unique-items-as-set`](typing-customization.md#use-unique-items-as-set) - Generate set types for arrays with uniqueItems constraint.
- [`--validation`](openapi-only-options.md#validation) - Enable validation constraints (deprecated, use --field-const...
- [`--validators`](template-customization.md#validators) - Add custom field validators to generated Pydantic v2 models.
- [`--version`](utility-options.md#version) - Show program version and exit
- [`--watch`](general-options.md#watch) - Watch input file(s) for changes and regenerate output automa...
- [`--watch-delay`](general-options.md#watch-delay) - Set debounce delay in seconds for watch mode.
- [`--wrap-string-literal`](template-customization.md#wrap-string-literal) - Wrap long string literals across multiple lines.
