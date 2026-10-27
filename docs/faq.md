# ❓ Frequently Asked Questions

## 📋 Schema Handling

### 🔀 oneOf/anyOf generates unexpected Union types

When using `oneOf` or `anyOf`, the generated models may not match your expectations. Use `--union-mode` to control how unions are generated:

```bash
# Smart union (Pydantic v2 only) - validates against types in order
datamodel-codegen --union-mode smart --output-model-type pydantic_v2.BaseModel ...

# Left-to-right validation
datamodel-codegen --union-mode left_to_right ...
```

See [CLI Reference: `--union-mode`](cli-reference/model-customization.md#union-mode) for details.

### 🔗 allOf doesn't merge properties as expected

Control how `allOf` schemas merge fields:

```bash
# Merge only constraints (minItems, maxItems, pattern, etc.) - default
datamodel-codegen --allof-merge-mode constraints ...

# Merge constraints + annotations (default, examples)
datamodel-codegen --allof-merge-mode all ...

# Don't merge any fields
datamodel-codegen --allof-merge-mode none ...
```

When primitive `allOf` branches are merged, combining `number` and `integer`
produces `integer` in every merge mode. Numeric type arrays may also include
`null`; only types shared by every branch are retained, in their original order.
An empty numeric/null intersection raises a schema error.

See [CLI Reference: `--allof-merge-mode`](cli-reference/typing-customization.md#allof-merge-mode) for details.

📎 Related: [#399](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/399)

### 📁 How to generate from multiple schema files?

Use a directory as input, or use `$ref` to reference other files:

```bash
# Generate from directory containing multiple schemas
datamodel-codegen --input schemas/ --output models/
```

For schemas with remote cross-file `$ref`, install an HTTP extra:

```bash
pip install 'datamodel-code-generator[http]'
```

See [HTTP backend selection](#http-backend-selection) for the stable and
experimental choices.

📎 Related: [#215](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/215)

### 🔤 YAML bool keywords (YES, NO, true, false) in string enums

YAML 1.1 treats unquoted keywords like `YES`, `NO`, `on`, `off`, `true`, `false` as boolean values. This tool preserves them as strings to avoid unexpected conversions in schema contexts:

```yaml
# Input YAML
enum:
  - YES
  - NO
  - NOT_APPLICABLE
```

```python
# Generated code (strings preserved)
class MyEnum(Enum):
    YES = "YES"
    NO = "NO"
    NOT_APPLICABLE = "NOT_APPLICABLE"
```

This matches the expected behavior when `type: string` is specified in your schema. If you need the previous behavior where YAML bool keywords were converted to Python booleans, please [open an issue](https://github.com/datamodel-code-generator/datamodel-code-generator/issues) describing your use case.

📎 Related: [#1653](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/1653), [#1766](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/1766), [#2338](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/2338)

---

## 🔍 Type Checking

### ⚠️ mypy complains about Field constraints

If mypy reports errors about `conint`, `constr`, or other constrained types, use `--field-constraints` or `--use-annotated`:

```bash
# Use Field(..., ge=0) instead of conint(ge=0)
datamodel-codegen --field-constraints ...

# Use Annotated[int, Field(ge=0)]
datamodel-codegen --use-annotated ...
```

See [Field Constraints](field-constraints.md) for more information.

### 🤔 Type checker doesn't understand generated types

Ensure you're using the correct target Python version:

```bash
datamodel-codegen --target-python-version 3.11 ...
```

This affects type syntax generation (e.g., `list[str]` vs `List[str]`, `X | Y` vs `Union[X, Y]`).

---

## 🏷️ Field Naming

### 🚫 Property names conflict with Python reserved words

Properties like `class`, `from`, `import` are automatically renamed with a `field_` prefix. Control this behavior:

```bash
# Custom prefix (default: "field")
datamodel-codegen --special-field-name-prefix my_prefix ...

# Remove special prefix entirely
datamodel-codegen --remove-special-field-name-prefix ...
```

### 🔣 Field names have special characters

JSON/YAML property names with spaces, dashes, or special characters are converted to valid Python identifiers. An alias is automatically generated to preserve the original name:

```python
class Model(BaseModel):
    my_field: str = Field(..., alias="my-field")
```

To disable aliases:

```bash
datamodel-codegen --no-alias ...
```

See [Field Aliases](aliases.md) for custom alias mappings.

### 🐍 Want snake_case field names from camelCase

```bash
datamodel-codegen --snake-case-field ...
```

This generates snake_case field names with camelCase aliases:

```python
class User(BaseModel):
    first_name: str = Field(..., alias="firstName")
```

---

## 🔄 Output Stability

### ⏰ Generated output changes on every run

The timestamp in the header changes on each run. Disable it for reproducible output:

```bash
datamodel-codegen --disable-timestamp ...
```

### 🌍 Output differs between environments

Ensure consistent formatting across environments:

```bash
# Explicitly set formatters
datamodel-codegen --formatters black isort ...

# Or disable formatting entirely for raw output
datamodel-codegen --formatters ...
```

Also ensure the same Python version and formatter configurations (`pyproject.toml`) are used.

### 🤖 CI fails because generated code is different

Use `--check` mode in CI to verify generated files are up-to-date:

```bash
datamodel-codegen --check --input schema.yaml --output models.py
```

This exits with code 1 if the output would differ, without modifying files.

### 🧹 Files from earlier runs remain in the output directory

Generation overwrites the files it writes and never deletes a file an earlier run wrote. Remove files that are no
longer generated yourself; `--check` of a directory output lists them as `EXTRA`.

A stale package can hide a new module. When a schema change turns a package of a directory output into a module, for
example `models/pets/` into `models/pets.py`, or a directory output `models/` gives way to a single-file output
`models.py`, the old directory stays, and Python imports a directory that holds an `__init__.py` in preference to the
module of the same name. Generation still writes the module and warns that the existing package directory shadows it,
naming both paths; remove the stale directory. A directory whose only `__init__` is a compiled `__init__.pyc` or an
extension module hides the module in the same way but is not reported. `--check` and `--diff-against` write nothing
and do not warn. `--disable-warnings` and Python warning filters silence the warning; a filter that turns it into an
error, such as `-W error::UserWarning`, stops the run before it writes anything.

---

## ⚡ Performance

### 🐢 Generation is slow for large schemas

For very large schemas with many models:

1. Use `--reuse-model` to deduplicate identical models
2. Consider splitting schemas into multiple files
3. Use `--disable-warnings` to reduce output

```bash
datamodel-codegen --reuse-model --disable-warnings ...
```

See [Model Reuse and Deduplication](model-reuse.md) for details.

---

## 🔧 Output Model Types

### 🤷 Which output model type should I use?

- **Pydantic v2** (`pydantic_v2.BaseModel`): ✨ Recommended for new projects. Better performance and modern API.
- **dataclasses**: Simple data containers without validation.
- **TypedDict**: Type hints for dict structures.
- **msgspec**: High-performance serialization.

See [Output Model Types](output-model-types.md) for a detailed comparison.

```bash
# For new projects
datamodel-codegen --output-model-type pydantic_v2.BaseModel ...
```

See [Output Model Types](output-model-types.md) for more details.

📎 Related: [#803](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/803)

### 💥 Generated code doesn't work with my Pydantic version

Ensure the output model type matches your installed Pydantic version:

```bash
# Check your Pydantic version
python -c "import pydantic; print(pydantic.VERSION)"

# Generate for Pydantic v2
datamodel-codegen --output-model-type pydantic_v2.BaseModel ...
```

---

## 🌐 Remote Schemas

### 📡 Cannot fetch schema from URL

Install the HTTP extra:

```bash
pip install 'datamodel-code-generator[http]'
```

The `http` extra is the stable choice. See
[HTTP backend selection](#http-backend-selection) if you want to use the
experimental HTTPX2 backend.

For authenticated endpoints:

```bash
datamodel-codegen --url https://api.example.com/schema.yaml \
    --http-headers "Authorization: Bearer TOKEN" \
    --output model.py
```

### 🔄 Which HTTP client backend is selected? {#http-backend-selection}

The HTTP extras install separate, matched client and transport stacks:

| Extra | Status | Client and transport |
|-------|--------|----------------------|
| `datamodel-code-generator[http]` | Stable, supported, and not deprecated | `httpx` + `httpcore` |
| `datamodel-code-generator[httpx2]` | Experimental | `httpx2` + `httpcore2` |

`datamodel-code-generator[all]` includes the stable `http` extra but
intentionally does not include the experimental `httpx2` extra.

Select the backend with `--http-backend {auto,httpx,httpx2}`, the corresponding
`http_backend` pyproject setting, or `HTTPBackend` in the public API:

1. `auto` is the default. It selects stable `httpx` when its client module is
   installed, including when both pairs are installed.
2. `auto` uses experimental `httpx2` + `httpcore2` only when the stable HTTPX
   client itself is unavailable.
3. `httpx` and `httpx2` require the selected pair. Explicit selections never
   fall back to a different backend.
4. A missing paired transport or another broken dependency in a selected stack
   raises its import error instead of hiding an invalid environment.
5. If `auto` finds neither client, the request fails with instructions to
   install an HTTP extra.

Selection and imports are lazy. Once `auto` selects a backend, it keeps that
selection for the process. Restart the process to change an already selected
backend after installing or removing an extra.

```toml title="pyproject.toml"
[tool.datamodel-codegen]
http-backend = "httpx2"
```

### 🔒 SSL certificate errors

For development/testing with self-signed certificates:

```bash
datamodel-codegen --url https://... --http-ignore-tls --output model.py
```

!!! warning "⚠️ Security Notice"
    Only use `--http-ignore-tls` in trusted environments.

---

## 📘 OpenAPI Specific

### 🌐 How can I request another HTTP library for generated API clients?

The experimental [Python client](python-client.md), generated with `--generate-client httpx2`, uses HTTPX2 for both
synchronous and asynchronous requests. If you need `aiohttp` or another HTTP library,
check [existing issues](https://github.com/datamodel-code-generator/datamodel-code-generator/issues), then
[start a discussion](https://github.com/datamodel-code-generator/datamodel-code-generator/discussions)
if your use case is not covered. Include the library you need, your use case, and whether you need synchronous
or asynchronous support. For performance-related requests, include a reproducible benchmark and the Python
and library versions.

This concerns requests made by generated API clients. [HTTP backend selection](#http-backend-selection)
controls fetching remote schemas during generation.

### 📝 How to handle readOnly/writeOnly properties?

Use `--read-only-write-only-model-type` to generate separate Request/Response models:

```bash
# Generate Request/Response models only
datamodel-codegen --read-only-write-only-model-type request-response ...

# Generate Base + Request + Response models
datamodel-codegen --read-only-write-only-model-type all ...
```

📎 Related: [#727](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/727)

### ❓ Why are nullable fields not Optional?

Use `--strict-nullable` to treat nullable fields as truly optional:

```bash
datamodel-codegen --strict-nullable ...
```

📎 Related: [#327](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/327)

---

## 🔧 Advanced

### 📦 How to use TypeAlias instead of RootModel?

Use `--use-type-alias` (experimental) to generate type aliases instead of root models:

```bash
datamodel-codegen --use-type-alias --output-model-type pydantic_v2.BaseModel ...
```

See [Root Models and Type Aliases](root-model-and-type-alias.md) for details.

📎 Related: [#2505](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/2505)

---

## Opt-in behaviour changes in 0.87.0

This section lists every change in generated behaviour that is available as an option or as a dated preset, with the schema shapes it affects, a before/after example and the release it appeared in.

The default output stays as in the previous release. A change that alters generated behaviour is never the new default, even when it is a fix or an improvement: it ships behind its own option. The only changes to the default output are changes that do not alter behaviour (formatting, redundant lines, import order) and fixes for inputs that crashed or produced code that could not be imported; see [Crash and import fixes in the default output](#crash-and-import-fixes-in-the-default-output).

### How to opt in

Pass the option of a section below to enable that change on its own:

```bash
datamodel-codegen --input schema.json --use-tuple-for-closed-arrays --output model.py
```

To enable every option in this section at once, use the `20261027` preset (see [Presets](presets.md)); `--use-typed-map-values-beside-other-types` takes effect only together with `--use-typed-nullable-map-values`, which no preset sets. Explicit options override preset-supplied values, and options can be added on top of a preset.

!!! note "Class"
    Every section below has the class **opt-in**: the option changes what the generated models accept or reject, how they are named or ordered, or which types they use. Without the option the output is byte-identical to 0.86.0.

See also: [Presets](presets.md), [Deprecations](deprecations.md), [CLI Reference](cli-reference/index.md).

### `--filter-literal-values`

| | |
|---|---|
| Option | `--filter-literal-values` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5100](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5100) |

**Affects:** `enum` or `const` values that fail the constraints written beside them (for example `maximum`). `--omit-redundant-constraints` implies this option.

Schema: `{"type": "object", "properties": {"level": {"type": "integer", "enum": [1, 5, 10, 50], "maximum": 10}}}`.

Without the option (same as 0.86.0):

```python
class Level(IntEnum):
    integer_1 = 1
    integer_5 = 5
    integer_10 = 10
    integer_50 = 50
```

With the option:

```python
class Level(IntEnum):
    integer_1 = 1
    integer_5 = 5
    integer_10 = 10
```

### `--read-const-beside-enum`

| | |
|---|---|
| Option | `--read-const-beside-enum` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5101](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5101), [#5102](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5102) |

**Affects:** a `const` written beside an `enum`. A value whose `enum` admits only some values is typed by the `const`; an `enum` that contains its `const` is generated as that `const` under `--use-single-value-enum-as-const`. `--use-single-value-enum-as-const` alone keeps its 0.86.0 output. A plain `Enum` and msgspec enums keep the enum type and warn that the other values are accepted.

Schema: `levels` is an array of `{"type": "integer", "enum": [1, 2, 3], "const": 2}`. A second case, `kind: {"type": "string", "enum": ["a"], "const": "a"}` with `--use-single-value-enum-as-const`, writes `kind: Literal['a'] | None` without the `Kind` enum class when `--read-const-beside-enum` is added.

Without the option (same as 0.86.0):

```python
class Model(BaseModel):
    levels: list[Level] | None = None
```

With the option:

```python
class Model(BaseModel):
    levels: list[Literal[Level.integer_2]] | None = None
```

### `--use-tuple-for-closed-arrays`

| | |
|---|---|
| Option | `--use-tuple-for-closed-arrays` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5103](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5103) |

**Affects:** `prefixItems` arrays closed by `items: false`, `unevaluatedItems: false`, a `false` position or `contains`, and `items` arrays closed by `additionalItems: false`. `--use-tuple-for-fixed-items` alone keeps its 0.86.0 output.

Schema: `point` is `{"type": "array", "prefixItems": [{"type": "number"}, {"type": "string"}], "minItems": 2, "items": false}`.

Without the option (same as 0.86.0):

```python
class Model(BaseModel):
    point: list[float | str] = Field(..., max_length=2, min_length=2)
```

With the option:

```python
class Model(BaseModel):
    point: tuple[float, str]
```

### `--pin-aliases-with-alias-generator`

| | |
|---|---|
| Option | `--pin-aliases-with-alias-generator` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5104](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5104) |

**Affects:** Pydantic v2 models and Pydantic dataclasses that use an `alias_generator`; with `--target-pydantic-version` older than 2.8, dataclasses whose config names a generator. Other output model types ignore the option.

Command: `--output-model-type pydantic_v2.dataclass --snake-case-field --target-pydantic-version 2 --extra-template-data data.json` with `{"#all#": {"alias_generator": "to_camel"}}`; the schema has the required properties `firstName` and `foo_bar`.

Without the option (same as 0.86.0):

```python
@dataclass
class AliasGeneratorModel:
    first_name: str
    foo_bar: str = Field(..., alias="foo_bar")
```

With the option:

```python
@dataclass
class AliasGeneratorModel:
    foo_bar: str
    first_name: str = Field(..., alias="firstName")
```

### `--tag-msgspec-by-payload-name`

| | |
|---|---|
| Option | `--tag-msgspec-by-payload-name` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5105](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5105) |

**Affects:** `msgspec.Struct` discriminated unions whose discriminator property has a name that differs from its Python name (for example `pet-kind`). `--use-msgspec-tagged-unions` already behaves this way. Other output model types ignore the option.

Schema (abridged): `Cat` and `Dog` each have a required property `pet-kind` with a `const`; `pet` is a `oneOf` of both with `discriminator.propertyName: "pet-kind"`.

Without the option (same as 0.86.0):

```python
class Cat(Struct, tag_field="pet_kind", tag="cat"):
    name: str | UnsetType = UNSET
```

With the option:

```python
class Cat(Struct, tag_field="pet-kind", tag="cat"):
    name: str | UnsetType = UNSET
```

### `--order-constructor-fields-by final-names`

| | |
|---|---|
| Option | `--order-constructor-fields-by final-names` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5106](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5106) |

**Affects:** `dataclasses.dataclass`, `pydantic_v2.dataclass` and `msgspec.Struct` subclasses that override an inherited, renamed field as required (the override keeps the position of the inherited field instead of becoming keyword-only). Other output model types ignore the option.

Two modules: `a.json` defines `Base` with a property `Bar`, `b.json` defines `Child` as `allOf: [{"$ref": "a.json"}]` with `Bar` required. Command: `--output-model-type pydantic_v2.dataclass --target-python-version 3.11`.

Without the option (same as 0.86.0):

```python
@dataclass
class Child(Base):
    Bar_1: a.Bar = Field(..., alias="Bar", kw_only=True)
    Zed: str | None = None
```

With the option:

```python
@dataclass
class Child(Base):
    Bar_1: a.Bar = Field(..., alias="Bar")
    Zed: str | None = None
```

### `--use-site-models-for-constrained-refs`

| | |
|---|---|
| Option | `--use-site-models-for-constrained-refs` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5107](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5107) |

**Affects:** a `$ref` that carries more assertions beside it, or in an `allOf` with it (constraints, an enum or a type). The referenced definition itself stays unchanged.

Schema: `quantity` is `allOf: [{"$ref": "#/$defs/Quantity"}]` with `maximum: 10`, `sizes` holds items `allOf: [{"$ref": "#/$defs/Size"}, {"enum": ["m", "l"]}]`. In 0.86.0 `maximum` and the narrowed enum are not enforced.

Without the option (same as 0.86.0):

```python
class Order(BaseModel):
    quantity: Quantity | None = None
    sizes: list[Size] | None = None
```

With the option:

```python
class Quantity1(RootModel[conint(ge=1, le=10)]):
    root: conint(ge=1, le=10)


class Size2(Enum):
    m = "m"
    l = "l"


class Order(BaseModel):
    quantity: Quantity1 | None = None
    sizes: list[Size2] | None = None
```

### `--intersect-allof-keywords`

| | |
|---|---|
| Option | `--intersect-allof-keywords` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5108](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5108), [#4951](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/4951) |

**Affects:** keywords combined through `allOf` values, `$ref` siblings or inherited overrides. Numeric and length constraints are intersected, enums narrowed to the values every part accepts, patterns conjoined, and `null` members dropped beside a type that excludes null (for example `kind: str | None` becomes `kind: str` when the child requires a `str`).

Schema: `Target` is `{"type": ["object", "null"], "additionalProperties": {"type": "string"}}` and `Holder` is `{"allOf": [{"$ref": "#/$defs/Target", "type": "object"}]}`. `type: object` excludes null, so only the map remains.

Without the option (same as 0.86.0):

```python
class Holder(BaseModel):
    pass
```

With the option:

```python
class Holder(RootModel[dict[str, str]]):
    root: dict[str, str]
```

### `--use-single-dict-for-object-maps`

| | |
|---|---|
| Option | `--use-single-dict-for-object-maps` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5113](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5113) |

**Affects:** objects without `properties` whose keys are constrained by `patternProperties` or `propertyNames`. The key pattern joins every pattern, the value is the union of the value types, and keys outside the patterns are typed by `additionalProperties`. A warning names the cases one dict cannot express exactly. The stricter pattern-map checks of `--generate-schema-validators` are under `--check-pattern-maps-strictly`.

Schema: `patternProperties` with `^i-` (string) and `^n-` (integer).

Without the option (same as 0.86.0):

```python
class Model(
    RootModel[dict[constr(pattern=r'^i-'), str] | dict[constr(pattern=r'^n-'), int]]
):
```

With the option:

```python
class Model(RootModel[dict[constr(pattern=r'^i-|^n-'), str | int]]):
```

### `--check-pattern-maps-strictly`

| | |
|---|---|
| Option | `--check-pattern-maps-strictly` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5152](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5152) |

**Affects:** `patternProperties` checked by `--generate-schema-validators`; the option does nothing without it. A declared property is also checked against every matching pattern whose schema its own does not imply (`x-id` beside `^x-` with `maxLength: 3` rejects `"abcd"`), the dict root model of an object without `properties` counts every key a pattern admits and takes its keys from `propertyNames`, and the model of an object without `properties` keeps the other types of its `type` array.

Schema: `labels` is `{"type": "object", "patternProperties": {"^l-": {"type": "integer"}}, "additionalProperties": false, "minProperties": 1}`.

Without the option (same as 0.86.0; every non-empty object is rejected):

```python
class Labels(_JsonSchemaRuntimeValidationBase, RootModel[dict[str, Any]]):
    root: dict[str, Any] = Field(..., max_length=0, min_length=1)
```

With the option:

```python
class Labels(_JsonSchemaRuntimeValidationBase, RootModel[dict[str, Any]]):
    root: dict[str, Any] = Field(..., min_length=1)
```

### `--read-unevaluated-properties`

| | |
|---|---|
| Option | `--read-unevaluated-properties` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5114](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5114) |

**Affects:** a schema-valued `unevaluatedProperties` on a schema that no applicator (`allOf`, `anyOf`, `oneOf`, `not`, `if`, `dependentSchemas`, `dependencies` or a reference) evaluates in place. It is read like `additionalProperties`: extra keys are typed, and an object without properties becomes a typed map.

Schema: `Resource` has a required `name` and `unevaluatedProperties: {"type": "string"}`.

Without the option (same as 0.86.0):

```python
class Resource(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
```

With the option:

```python
class Resource(BaseModel):
    model_config = ConfigDict(extra="allow")
    __annotations__ = {"__pydantic_extra__": Dict[str, str]}
    name: str
```

### `--dedupe-request-response-variants`

| | |
|---|---|
| Option | `--dedupe-request-response-variants` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5115](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5115) |

**Affects:** `--read-only-write-only-model-type request-response` and `all`. The read and write variants are decided from the schema graph: no unreferenced duplicate variants, stable numbering, and the inline models of an `allOf` parent are written once. Some recursive schemas cannot be generated without the option, and the error names it.

Schema (OpenAPI): `Child` inherits `Parent` through `allOf`, `Parent.id` is `readOnly`, and `Parent.payload` is an inline object. Command: `--read-only-write-only-model-type request-response`. `Payload1` is written but never referenced in the first block.

Without the option (same as 0.86.0):

```python
class Payload1(BaseModel):
    value: Value | None = None


class Payload2(BaseModel):
    value: Value | None = None


class ParentRequest(BaseModel):
    payload: Payload2 | None = None
```

With the option:

```python
class Payload1(BaseModel):
    value: Value | None = None


class ParentRequest(BaseModel):
    payload: Payload1 | None = None
```

### `--order-constructor-fields-by written-defaults`

| | |
|---|---|
| Option | `--order-constructor-fields-by written-defaults` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5116](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5116) |

**Affects:** `msgspec.Struct` output under `--strip-default-none`. Every optional field goes after the required fields in schema order, which changes the order of the positional constructor arguments. Without the option a struct is reordered only where a required field would otherwise follow a default. Under `--use-default-factory-for-optional-nested-models` the option also orders dataclass and Pydantic dataclass fields by the nested-model default factories they write, so a factory that a reference cycle drops moves no field, which it could without the option, as in 0.86.0. Other output model types, and runs without `--strip-default-none`, ignore the option.

Command: `--output-model-type msgspec.Struct --strip-default-none`. Schema: optional `d` (string, default `"x"`), `t` (`[string, null]`) and `q` (string).

Without the option (same as 0.86.0):

```python
class Spelling(Struct):
    t: str | None | UnsetType = UNSET
    q: str | UnsetType = UNSET
    d: str | UnsetType = "x"
```

With the option:

```python
class Spelling(Struct):
    d: str | UnsetType = "x"
    t: str | None | UnsetType = UNSET
    q: str | UnsetType = UNSET
```

### `--read-nullable-beside-type-list`

| | |
|---|---|
| Option | `--read-nullable-beside-type-list` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5117](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5117), [#5052](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5052) |

**Affects:** a `type` list that holds an object and another type, with `nullable: true` beside it (OpenAPI 3.0), under `--strict-nullable` or `--use-missing-sentinel`. The root model admits `None`, as it does when the list holds `"null"`.

Schema: `U: {type: [object, string], nullable: true, properties: {x: {type: integer}}}`, with `--use-missing-sentinel`.

Without the option (same as 0.86.0):

```python
class U1(RootModel[U | str | MISSING]):
    root: U | str | MISSING = MISSING
```

With the option:

```python
class U1(RootModel[U | str | None | MISSING]):
    root: U | str | None | MISSING = MISSING
```

### `--keep-field-defaults-off-branch-roots`

| | |
|---|---|
| Option | `--keep-field-defaults-off-branch-roots` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5118](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5118), [#4987](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/4987) |

**Affects:** an `anyOf` or `oneOf` branch with constraints (for example `{type: number, minimum: 0}` beside `{type: "null"}`) in a property with `default: null`, under `--field-constraints` or `--use-annotated`. The default stays with the property and the branch's root model requires its value.

Schema (OpenAPI 3.1): `weight: {anyOf: [{type: number, minimum: 0}, {type: "null"}], default: null}`, with `--field-constraints`.

Without the option (same as 0.86.0):

```python
class Weight(RootModel[float]):
    root: float = Field(None, ge=0.0)
```

With the option:

```python
class Weight(RootModel[float]):
    root: float = Field(..., ge=0.0)
```

### `--omit-redundant-reference-null`

| | |
|---|---|
| Option | `--omit-redundant-reference-null` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5120](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5120), [#5081](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5081) |

**Affects:** a field, array item, map value or union member that refers to a model whose own type already admits `None` (a `RootModel` whose root admits `None`, or a `type: "null"` model). `| None` is written at the use only when the target's class cannot hold null. A null item is then parsed to `Name(root=None)` instead of `None`. Type aliases, enums with a `None` member and plain object models are unchanged.

Command: `--input-file-type openapi --output-model-type pydantic_v2.BaseModel --strict-nullable`, with `Name: {type: [string, "null"]}` (`RootModel[str | None]`) used as array items.

Without the option (same as 0.86.0):

```python
class Model(BaseModel):
    names: list[Name | None]
```

With the option:

```python
class Model(BaseModel):
    names: list[Name]
```

### `--use-typed-map-values-beside-other-types`

| | |
|---|---|
| Option | `--use-typed-map-values-beside-other-types` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5094](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5094), [#5127](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5127) |

**Affects:** a schema whose `type` array lists `object` together with other types than `null` (for example `[object, string]`) and has `additionalProperties`, under `--use-typed-nullable-map-values`. With both options its object member is typed as its schema says, as `--use-typed-nullable-map-values` already types it for `[object, null]`. `--use-typed-nullable-map-values` alone keeps the object member `dict[str, Any]`, as in 0.86.0, and the option changes nothing without `--use-typed-nullable-map-values`.

Schema: `tags` is `{"type": ["object", "string"], "additionalProperties": {"type": "integer"}}`.

With `--use-typed-nullable-map-values` alone (same as 0.86.0):

```python
class Model(BaseModel):
    tags: dict[str, Any] | str | None = None
```

With `--use-typed-nullable-map-values --use-typed-map-values-beside-other-types`:

```python
class Model(BaseModel):
    tags: dict[str, int] | str | None = None
```

### `--keep-enum-member-values`

| | |
|---|---|
| Option | `--keep-enum-member-values` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5133](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5133) |

**Affects:** an enum whose class gets a value base from its schema type, an `IntEnum` or `StrEnum` (`--use-specialized-enum`) or a `(T, Enum)` (`--use-subclass-enum`), while that base changes a member value: `2.5` beside integer members becomes `2`, and `1` in a `(str, Enum)` becomes `'1'`. With the option such an enum is a plain `Enum` that keeps every value as written; members the base keeps, such as `1.0` in an `IntEnum`, keep the base, and msgspec keeps the 0.86.0 class where it could not decode the plain `Enum`.

Schema: `{"type": "object", "properties": {"level": {"type": "integer", "enum": [1, 2.5]}}}`.

Without the option (same as 0.86.0; the member `2.5` is `2`, so the model rejects `2.5` and accepts `2`):

```python
class Level(IntEnum):
    integer_1 = 1
    integer_2_5 = 2.5
```

With the option:

```python
class Level(Enum):
    integer_1 = 1
    integer_2_5 = 2.5
```

### `--omit-redundant-constraints`

| | |
|---|---|
| Option | `--omit-redundant-constraints` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#4525](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/4525), [#4875](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/4875) |

**Affects:** a constraint written again beside a type that already enforces it: the bound of a zero-bound class such as `NonNegativeInt` (also needs `--use-non-positive-negative-number-constrained-types`, which no preset sets), and the length and number constraints a `const` or `enum` `Literal` meets by its values. It implies `--filter-literal-values`.

Schema: `{"title": "Model", "type": "object", "properties": {"count": {"type": ["integer", "null"], "minimum": 0}, "code": {"type": "string", "const": "abc", "minLength": 1}}}`. Command: `--use-annotated --use-non-positive-negative-number-constrained-types`.

Without the option (same as 0.86.0):

```python
class Model(BaseModel):
    count: Annotated[NonNegativeInt | None, Field(ge=0)] = None
    code: Annotated[Literal["abc"] | None, Field(min_length=1)] = None
```

With the option:

```python
class Model(BaseModel):
    count: NonNegativeInt | None = None
    code: Literal["abc"] | None = None
```

### `--openapi-as-definitions paths-bodies`

| | |
|---|---|
| Option | `--openapi-as-definitions paths-bodies` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5155](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5155) |

**Affects:** request and response bodies in the `paths` and `webhooks` scopes (they need `--openapi-scopes paths` / `webhooks`, which no preset sets). The body gets the model a schema definition of the same body gets: an object body with a `type` array such as `[object, null]`, or with map keywords and no `type`, becomes a model instead of an inline type. This is separate from the crash fix for object bodies in the legacy `paths` scope (#5024).

Schema (abridged): the `POST /pets` request body is `{"type": ["object", "null"], "additionalProperties": {...}}`. Command: `--input-file-type openapi --openapi-scopes paths --output-model-type pydantic_v2.BaseModel`.

Without the option (same as 0.86.0), the body is typed inline and a document whose only bodies are such schemas fails:

```
Models not found in the input data; request or response bodies of the paths or webhooks scope are typed inline, and --openapi-as-definitions paths-bodies gives them the models their schema definitions get
```

With the option:

```python
class PetsPostRequest(RootModel[dict[str, Any] | None]):
    root: dict[str, Any] | None
```

### `--openapi-as-definitions ref-components`

| | |
|---|---|
| Option | `--openapi-as-definitions ref-components` |
| Preset | `20261027` |
| Class | opt-in |
| Release | 0.87.0 |
| Issue | [#5176](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5176), [#5162](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5162) |

**Affects:** an OpenAPI component written as a `$ref` to a recursive schema with sibling keywords, such as `minProperties`. The component gets the root model that the same schema gets as a `$defs` entry, references to it name that model, and a component that nothing references is generated too. Such a component in another document gets that model with or without the option.

Schema: `Node: {type: object, properties: {children: {type: array, items: {$ref: '#/components/schemas/Tree'}}}}`, `Tree: {$ref: '#/components/schemas/Node', description: A tree., minProperties: 1}`, `Holder: {type: object, properties: {tree: {$ref: '#/components/schemas/Tree'}}}`.

Without the option (same as 0.86.0, where a reference to `Tree` failed with "A Parser can not resolve classes"; see [Crash and import fixes in the default output](#crash-and-import-fixes-in-the-default-output)), `Tree` gets no model and references to it are typed by `Node`:

```python
class Node(BaseModel):
    children: list[Node] | None = None


class Holder(BaseModel):
    tree: Node | None = None
```

With the option:

```python
class Node(BaseModel):
    children: list[Tree] | None = None


class Holder(BaseModel):
    tree: Tree | None = None


class Tree(RootModel[Node]):
    root: Node = Field(..., description="A tree.")
```

### Crash and import fixes in the default output

These fixes apply without any option: the old result could not be generated or imported, so nothing could depend on it.

- Map keys that reference a root model are typed by the root type instead of failing ([#5017](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/5017)).
- Object bodies in the `paths` scope of the legacy parser now get models ([#5024](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/5024)).
- `TypedDict` `extra_items` names the final class, so the module imports ([#5022](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/5022)).
- Non-finite float enum members are written importably ([#4954](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4954)).
- GraphQL subclass enums are written as importable `StrEnum` ([#4970](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4970)).
- An enum whose value base cannot construct one of its members, such as `'a'` in an `IntEnum` or the strings of a `format: byte` enum in a `(bytes, Enum)`, is written as a plain `Enum` instead of a class that fails at import ([#4973](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4973)).
- Discriminator tags with float values, and bool values for msgspec, no longer crash generation or import; the union is written without a discriminator ([#5053](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5053), [#5086](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/5086)).
- Schemas merged from another document resolve their `$ref`s in their own document: "A Parser can not resolve classes" ([#5046](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5046)) and `$ref file not found` for an inherited property narrowed in another directory ([#5068](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5068)).
- A `$ref` to an `$anchor` inside a `oneOf` branch resolves instead of failing with `Unresolved $id reference` ([#5056](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5056), fixed by [#5092](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/5092)).
- A `ValidationError` for an `allOf` child and parent that both declare a property named like a schema keyword is fixed ([#5069](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/5069)).
- Long `$ref` alias chains no longer depend on the Python recursion limit; schemas nested too deeply stop with a clear error ([#5076](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5076)).
- A reference to a schema written as a `$ref` to a recursive schema with constraint siblings, which gets no model of its own (an OpenAPI component, a property), is typed by that `$ref`'s target instead of failing with "A Parser can not resolve classes" ([#5162](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/5162)).

## 📖 See Also

- 🖥️ [CLI Reference](cli-reference/index.md) - Complete option documentation
- ⚙️ [pyproject.toml Configuration](pyproject_toml.md) - Configure options via file
- 🐛 [GitHub Issues](https://github.com/datamodel-code-generator/datamodel-code-generator/issues) - Report bugs or request features
- 💬 [Discussions](https://github.com/datamodel-code-generator/datamodel-code-generator/discussions) - Ask questions and share ideas
