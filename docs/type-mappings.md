<!-- related-cli-options: --type-mappings, --strict-types, --output-datetime-class, --use-pendulum, --use-decimal-for-multiple-of -->

# Type Mappings and Custom Types

datamodel-code-generator allows you to customize how schema types are mapped to Python types. This is essential for projects with specific type requirements, datetime handling preferences, or third-party library integrations.

## Quick Overview

| Option | Description |
|--------|-------------|
| `--type-mappings` | Map schema types to custom Python types |
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
