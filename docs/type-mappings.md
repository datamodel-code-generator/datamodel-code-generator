<!-- related-cli-options: --type-mappings, --read-format-as-annotation, --untyped-value-type, --strict-types, --output-datetime-class, --use-pendulum, --use-decimal-for-multiple-of -->

# Type Mappings and Custom Types

datamodel-code-generator allows you to customize how schema types are mapped to Python types. This is essential for projects with specific type requirements, datetime handling preferences, or third-party library integrations.

## Quick Overview

| Option | Description |
|--------|-------------|
| `--type-mappings` | Map schema types to custom Python types |
| `--read-format-as-annotation` | Type each value by its `type` keyword, reading `format` as an annotation |
| `--untyped-value-type` | Write the values a schema leaves untyped as one type, such as `pydantic.JsonValue` |
| `--strict-types` | Use Pydantic strict types for validation |
| `--output-datetime-class` | Choose datetime output type |
| `--use-pendulum` | Use Pendulum library for datetime types |
| `--use-decimal-for-multiple-of` | Use Decimal for multipleOf constraints |
| `format: "email"` | Email validation (requires `email-validator`) |
| `format: "ulid"` | ULID type support (requires `python-ulid`) |

---

## `--type-mappings`

Maps schema type+format combinations to other format names. This allows you to override how specific formats are interpreted.

### Format

```bash
--type-mappings <type+format>=<target_format> [<type+format>=<target_format> ...]
```

### Basic Examples

```bash
# Map binary format to string (generates str instead of bytes)
datamodel-codegen --input schema.json --output models.py \
  --type-mappings "binary=string"

# Map email format to string (generates str instead of EmailStr)
datamodel-codegen --input schema.json --output models.py \
  --type-mappings "string+email=string"

# Multiple mappings
datamodel-codegen --input schema.json --output models.py \
  --type-mappings "string+email=string" "string+uuid=string"
```

### Mapping Syntax

| Syntax | Description | Example |
|--------|-------------|---------|
| `format=target` | Map format (assumes string type) | `binary=string` |
| `type+format=target` | Map type with format | `string+email=string` |

### Avoiding Pydantic Optional Extras

Some Pydantic types require optional dependencies. Use `--type-mappings` to generate plain types instead:

```bash
# Avoid pydantic[email] dependency (EmailStr requires email-validator)
--type-mappings "string+email=string" "string+idn-email=string"

# Generate str instead of UUID for uuid format
--type-mappings "string+uuid=string"
```

### Available Target Formats

| Target | Generated Type |
|--------|----------------|
| `string` | `str` |
| `integer` | `int` |
| `number` | `float` |
| `boolean` | `bool` |
| `binary` | `bytes` |
| `date` | `datetime.date` |
| `date-time` | `datetime.datetime` |
| `uuid` | `UUID` |
| `email` | `EmailStr` |
| `uri` | `AnyUrl` |

### pyproject.toml Configuration

```toml
[tool.datamodel-codegen]
type-mappings = [
    "string+email=string",
    "string+idn-email=string",
    "binary=string",
]
```

The `minLength` and `maxLength` of a `format: binary` string, written as `bytes`, count octets, as OpenAPI 3.1.1
specifies for unencoded binary data, so `maxLength: 3` rejects `"ééé"`, three characters in six octets.

msgspec decodes a JSON string into `bytes` as base64, so in `msgspec.Struct` output a `format: binary` field rejects
`"abc"` and decodes `"aGk="` into `b"hi"`; `--type-mappings binary=string` writes the field as `str`, which keeps the
text as written.

---

## `--read-format-as-annotation`

JSON Schema reads `format` as an annotation: a validator does not reject a string whose format does not match, and
the value stays the JSON string. By default each known format selects a type instead, as in 0.96.0, so the models
reject or change data the schema accepts: `email` is `EmailStr`, which needs `email-validator` to import and leaves
out `maxLength`; `date-time` is `AwareDatetime`, which rejects a date-time without an offset; `password` is
`SecretStr`, which dumps `**********`; and `uri` is `AnyUrl`, which dumps `https://example.com/` for
`https://example.com`.

With `--read-format-as-annotation` (`read-format-as-annotation = true`, `read_format_as_annotation=True`) a string,
integer or number schema takes the type its `type` keyword gives, `str`, `int` or `float`, with its own `minLength`,
`maxLength`, `pattern` and bounds, so the models accept every value the schema accepts and dump it as written. An
unknown format gives no warning.

```json
{
  "title": "CreateContact",
  "type": "object",
  "properties": {
    "email": {"type": "string", "format": "email", "maxLength": 64},
    "website": {"type": "string", "format": "uri"},
    "remindAt": {"type": "string", "format": "date-time"},
    "apiKey": {"type": "string", "format": "password"}
  },
  "required": ["email", "remindAt"]
}
```

```python
class CreateContact(BaseModel):
    email: constr(max_length=64)
    website: str | None = None
    remindAt: str
    apiKey: str | None = None
```

Two formats still select a type:

- `format: binary` stays `bytes`: raw octets are not a JSON string. `--type-mappings binary=string` writes `str`.
- A format that a `--type-mappings` entry names for a type the schema declares keeps that mapping:
  `--type-mappings date-time=date-time` keeps the `date-time` strings typed, and `--output-datetime-class` then
  applies to them. A schema that declares no `type`, such as an allOf part, has no such entry.

The other options apply as to plain values: `--strict-types str` writes `StrictStr`, `--field-constraints` and
`--use-annotated` write the schema's own keywords into `Field(...)` (the hostname pattern a `hostname` format adds is
not written), `--use-subclass-enum` subclasses `str` for an enum of formatted strings, and the default of a
`format: decimal` number under `--deserialize-default-values decimal` is a float, not a `Decimal(...)`. `--output-datetime-class`, `--output-date-class`, `--use-pendulum`,
`--use-standard-primitive-types`, `--use-encoded-str-for-constrained-base64` and `--use-strict-str-for-base64` have no
effect on the formats read as annotations. `contentMediaType` and `--use-bytes-for-content-media-type` are not
`format` and work as without the option. The format itself is not written into the models.

The option reads JSON Schema, OpenAPI, the JSON Schema payloads of AsyncAPI and MCP tool schemas, with every document
they reference. Avro, Protocol Buffers and XML Schema inputs, and AsyncAPI payloads converted from them, keep the
types of their formats: there the format carries the type of the source, such as an Avro `timestamp-millis`.

---

## `--untyped-value-type`

A value the schema leaves untyped is any JSON value: `{}`, `true`, a schema without `type`, the values of an object
without properties and the items of an array without items. By default it is written as `Any`, and under
`--use-object-type` the values and items of such containers as `object`, as in 0.96.0.

`--untyped-value-type <type>` (`untyped-value-type = "pydantic.JsonValue"`, `untyped_value_type="pydantic.JsonValue"`)
writes each of them as the given type, with the structure as without the option:

- `pydantic.JsonValue` (Pydantic 2.5 or later), which type checkers read as
  `list[JsonValue] | dict[str, JsonValue] | str | bool | int | float | None`;
- `object`;
- `Any`, which writes `Any` also where `--use-object-type` writes `object`;
- the dotted import path of a type of your own, such as a recursive alias for `typing.TypedDict` output, which the
  models import (`mypkg._json.JsonValue` is written `from mypkg._json import JsonValue`).

Any other value is refused.

```json
{
  "title": "Event",
  "type": "object",
  "properties": {
    "payload": {},
    "context": {"type": "object"},
    "tags": {"type": "array"},
    "attributes": {"type": "object", "additionalProperties": {}}
  },
  "required": ["payload"]
}
```

```python
from pydantic import BaseModel, JsonValue


class Event(BaseModel):
    payload: JsonValue
    context: dict[str, JsonValue] | None = None
    tags: list[JsonValue] | None = None
    attributes: dict[str, JsonValue] | None = None
```

Every untyped `Any` is written so: a field, a union member (`str | JsonValue`), a dict value, a list item, the root of
a root model, the value of a type alias and `__pydantic_extra__`. The models parse a JSON document to the same values
as with `Any`; Python values that are no JSON values, such as a tuple, a set or a `datetime`, are rejected, and
`model_json_schema()` writes `{"$ref": "#/$defs/JsonValue"}` with `"JsonValue": {}` where it wrote `{}`.

- It takes precedence over `--use-object-type`, except in `msgspec.Struct` output.
- `--type-overrides`, `x-python-type` and `x-python-import` keep their types, `typing.Any` included.
- `msgspec.Struct` output keeps `Any`: msgspec decodes neither `pydantic.JsonValue` nor a recursive alias, and decodes
  `Any` into JSON values already.
- A definition named like the imported type is renamed by the rule for names an import takes: `$defs/JsonValue: {}`
  becomes `class JsonValueModel(RootModel[JsonValue])`, its references follow. With `--collapse-root-models` the root
  collapses into `JsonValue` at its references, and `--type-overrides '{"JsonValue": "pydantic.JsonValue"}'` leaves
  no class.
- With `--target-pydantic-version 2`, a run that writes `pydantic.JsonValue` warns that the models need Pydantic 2.5.
- The type is imported only by the modules that write an untyped value.

The practical presets dated 20261113 set `untyped-value-type = "pydantic.JsonValue"` for Pydantic output.

---

## `--strict-types`

Generates Pydantic strict types that don't perform type coercion during validation.

### Available Strict Types

| Value | Strict Type | Rejects |
|-------|-------------|---------|
| `str` | `StrictStr` | Integers, floats |
| `int` | `StrictInt` | Strings, floats |
| `float` | `StrictFloat` | Strings, integers |
| `bool` | `StrictBool` | Strings, integers |
| `bytes` | `StrictBytes` | Strings |

A `format: byte` string stays `Base64Str`, or `Base64Bytes` with `--type-mappings string+byte=binary`, which decode
base64 text, also with `--strict-types str` or `bytes`. With `minLength` or `maxLength`, such bytes are written as
`Annotated[bytes, Field(max_length=8), EncodedBytes(encoder=Base64Encoder)]`, which checks the lengths on the base64
text, as JSON Schema counts them, before decoding it. Pydantic checks them so from 2.10 on, and an explicit
`--target-pydantic-version 2` warns about each such field. Pydantic cannot check a `pattern` on the text of such bytes,
so `--field-constraints` and `--use-annotated` leave it out with a warning.

### Without `--strict-types`

```python
class User(BaseModel):
    id: int  # Accepts "123" and converts to 123
    name: str  # Accepts 123 and converts to "123"
    active: bool  # Accepts 1 and converts to True
```

### With `--strict-types`

```bash
datamodel-codegen --input schema.json --output models.py \
  --strict-types str int bool
```

```python
from pydantic import StrictBool, StrictInt, StrictStr


class User(BaseModel):
    id: StrictInt  # Rejects "123", requires integer
    name: StrictStr  # Rejects 123, requires string
    active: StrictBool  # Rejects 1, requires boolean
```

### When to use

- API validation where type coercion is undesirable
- Data pipelines requiring exact types
- Security-sensitive applications
- Testing environments requiring strict type checking

---

## `--output-datetime-class`

Controls the Python type used for `date-time` formatted strings.

| Value | Output Type | Description |
|-------|-------------|-------------|
| `datetime` | `datetime.datetime` | Standard library datetime (default) |
| `AwareDatetime` | `pydantic.AwareDatetime` | Requires timezone info |
| `NaiveDatetime` | `pydantic.NaiveDatetime` | Rejects timezone info |

### Default behavior

```python
from datetime import datetime


class Event(BaseModel):
    created_at: datetime  # Accepts both aware and naive
```

### AwareDatetime (recommended for APIs)

```bash
datamodel-codegen --input schema.json --output models.py \
  --output-datetime-class AwareDatetime
```

```python
from pydantic import AwareDatetime


class Event(BaseModel):
    created_at: AwareDatetime  # Requires timezone, e.g., 2024-01-01T00:00:00Z
```

### NaiveDatetime

```bash
datamodel-codegen --input schema.json --output models.py \
  --output-datetime-class NaiveDatetime
```

```python
from pydantic import NaiveDatetime


class Event(BaseModel):
    created_at: NaiveDatetime  # Rejects timezone, e.g., 2024-01-01T00:00:00
```

### When to use each

| Use Case | Recommended Class |
|----------|-------------------|
| REST APIs | `AwareDatetime` |
| Database models | `datetime` or `NaiveDatetime` |
| Logs with UTC timestamps | `AwareDatetime` |
| Local time applications | `NaiveDatetime` |

---

## `--use-pendulum`

Uses [Pendulum](https://pendulum.eustace.io/) library types instead of standard library datetime.

```bash
pip install pendulum
datamodel-codegen --input schema.json --output models.py --use-pendulum
```

### Output

```python
import pendulum


class Event(BaseModel):
    created_at: pendulum.DateTime
    date: pendulum.Date
    time: pendulum.Time
```

### Benefits of Pendulum

- Timezone handling is simpler and more intuitive
- Human-friendly datetime manipulation
- Better serialization defaults
- Immutable by default

### When to use

- Projects already using Pendulum
- Applications requiring complex datetime manipulation
- When timezone handling is a priority

---

## Email Format Support

The generator supports the `email` and `idn-email` string formats, which generate Pydantic's `EmailStr` type.

!!! warning "Required Dependency"
    The `email` format requires the `email-validator` package to be installed:
    ```bash
    pip install email-validator
    ```
    Or install Pydantic with the email extra:
    ```bash
    pip install pydantic[email]
    ```

### Schema Example

```json
{
  "type": "object",
  "properties": {
    "email": {
      "type": "string",
      "format": "email"
    }
  }
}
```

### Output

```python
from pydantic import BaseModel, EmailStr


class MyModel(BaseModel):
    email: EmailStr
```

### Avoiding the Dependency

If you don't want to install `email-validator`, you can map email formats to plain strings:

```bash
datamodel-codegen --input schema.json --output models.py \
  --type-mappings "string+email=string" "string+idn-email=string"
```

This generates `str` instead of `EmailStr`.

---

## ULID Format Support

The generator supports the `ulid` string format, which generates [`python-ulid`](https://github.com/mdomke/python-ulid) types.

!!! warning "Required Dependency"
    The `ulid` format requires the `python-ulid` package to be installed:
    ```bash
    pip install python-ulid
    ```
    For Pydantic integration, use:
    ```bash
    pip install python-ulid[pydantic]
    ```

### Schema Example

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "format": "ulid"
    }
  }
}
```

### Output

```python
from ulid import ULID
from pydantic import BaseModel


class MyModel(BaseModel):
    id: ULID
```

### What is ULID?

ULID (Universally Unique Lexicographically Sortable Identifier) is an alternative to UUID that offers:

- **Lexicographic sorting** - ULIDs sort naturally by creation time
- **Compactness** - 26 characters vs 36 for UUID
- **URL-safe** - Uses Crockford's Base32 encoding
- **Timestamp encoded** - First 10 characters encode creation time

### When to use

- Distributed systems requiring time-ordered IDs
- Applications where database index performance matters
- When you need both uniqueness and sortability

---

## `--use-decimal-for-multiple-of`

Uses `Decimal` type for numbers with `multipleOf` constraints to avoid floating-point precision issues.

### The Problem

```yaml
properties:
  price:
    type: number
    multipleOf: 0.01  # Currency precision
```

Without this option, floating-point arithmetic can cause validation issues:

```python
# 0.1 + 0.2 = 0.30000000000000004 in floating-point
price = 0.30000000000000004  # May fail multipleOf validation
```

### Solution

```bash
datamodel-codegen --input schema.json --output models.py \
  --use-decimal-for-multiple-of
```

```python
from decimal import Decimal


class Product(BaseModel):
    price: Decimal  # Exact decimal arithmetic
```

### When to use

- Financial applications
- Scientific calculations requiring precision
- Any schema with `multipleOf` constraints

### Forms and strict types

The default Pydantic output writes `condecimal(...)`. With `--field-constraints` or `--use-annotated` the field is a
`float` with its constraints in `Field()`. Add `--use-decimal-in-field-forms` (set by the 20261027 presets) to make it
a `Decimal` with its constraints in `Field()` too, for example `Annotated[Decimal, Field(ge=0, multiple_of=0.01)]`.
With that option, under `--strict-types float` the `Decimal` is strict: `condecimal(..., strict=True)`, or
`strict=True` among the `Field()` arguments. It rejects Python floats and, as Pydantic's strict `Decimal` does in JSON,
accepts JSON numbers and numeric strings. A custom `DataTypeManager` receives every constraint of the
schema in the field forms too, with `field_constraints=True`; the base class ignores them.

---

## Common Patterns

### Pattern 1: Minimal dependencies

Avoid optional Pydantic dependencies by mapping special formats to plain types:

```bash
datamodel-codegen --input schema.json --output models.py \
  --type-mappings "string+email=string" "string+idn-email=string"
```

### Pattern 2: Strict API validation

```bash
datamodel-codegen --input schema.json --output models.py \
  --strict-types str int float bool \
  --output-datetime-class AwareDatetime \
  --field-constraints
```

### Pattern 3: Financial application

```bash
datamodel-codegen --input schema.json --output models.py \
  --use-decimal-for-multiple-of \
  --strict-types str int
```

### Pattern 4: Pendulum datetime handling

```bash
datamodel-codegen --input schema.json --output models.py \
  --use-pendulum \
  --strict-types str int
```

---

## Type Mapping Reference

### Common Format Mappings

| Schema Format | Default Type | With `--type-mappings "format=string"` |
|---------------|--------------|----------------------------------------|
| `email` | `EmailStr` | `str` |
| `idn-email` | `EmailStr` | `str` |
| `uuid` | `UUID` | `str` |
| `ulid` | `ULID` (requires `python-ulid`) | `str` |
| `uri` | `AnyUrl` | `str` |
| `binary` | `bytes` | `str` |

!!! note "Other type customization options"
    For datetime types, use `--output-datetime-class` or `--use-pendulum` instead of `--type-mappings`.

---

## See Also

- [CLI Reference: `--type-mappings`](cli-reference/typing-customization.md#type-mappings)
- [CLI Reference: `--strict-types`](cli-reference/typing-customization.md#strict-types)
- [CLI Reference: `--output-datetime-class`](cli-reference/typing-customization.md#output-datetime-class)
- [CLI Reference: `--use-pendulum`](cli-reference/typing-customization.md#use-pendulum)
- [Field Constraints](field-constraints.md)
- [Python Version Compatibility](python-version-compatibility.md)
