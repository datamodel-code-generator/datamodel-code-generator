# 🪶 Generate from Apache Avro Schema

Generate Python models from JSON-encoded Apache Avro schemas (`.avsc`).

!!! warning "Experimental"
    Apache Avro schema input support is experimental and may change as real-world usage is validated.

## 🚀 Quick Start

```bash
datamodel-codegen \
    --input user.avsc \
    --input-file-type avro \
    --output-model-type pydantic_v2.BaseModel \
    --output model.py
```

## 📝 Example

**user.avsc**
```json
{
  "type": "record",
  "name": "User",
  "namespace": "example.avro",
  "doc": "A user record.",
  "fields": [
    {
      "name": "id",
      "type": {
        "type": "string",
        "logicalType": "uuid"
      }
    },
    {
      "name": "name",
      "type": "string",
      "doc": "Display name"
    },
    {
      "name": "email",
      "type": ["null", "string"],
      "default": null
    },
    {
      "name": "roles",
      "type": {
        "type": "array",
        "items": {
          "type": "enum",
          "name": "Role",
          "symbols": ["admin", "member"]
        }
      },
      "default": []
    }
  ]
}
```

**✨ Generated model.py**
```python
from __future__ import annotations

from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class Role(Enum):
    admin = "admin"
    member = "member"


class User(BaseModel):
    id: UUID
    name: str = Field(..., description="Display name")
    email: Optional[str] = None
    roles: list[Role] = []
```

## Supported Avro Schema Features

The Avro parser supports the schema constructs needed to generate Python models:

| Avro construct | Model generation behavior |
|----------------|---------------------------|
| `record` | Generates a Python model class with fields |
| `enum` | Generates an enum class |
| `array` | Generates a list field or root list model |
| `map` | Generates a dict field or root dict model |
| `fixed` | Generates a fixed-length binary string model |
| `union` | Generates union types; nullable unions such as `["null", "string"]` become optional fields |
| Named types | Resolves `name`, `namespace`, fullnames, nested named types, and references |
| Field metadata | Preserves `doc`, `default`, `aliases`, and `order` where applicable |
| Logical types | Maps Avro logical types to Python-friendly formats where possible |

Supported primitive types are `null`, `boolean`, `int`, `long`, `float`, `double`, `bytes`, and `string`.

## Logical Types

Avro logical types are mapped through the generator's normal type handling:

| Avro logical type | Typical generated Python type |
|-------------------|-------------------------------|
| `decimal`, `big-decimal` | `Decimal` |
| `uuid` | `UUID` |
| `date` | `date` |
| `time-millis`, `time-micros` | `time` |
| `timestamp-millis`, `timestamp-micros`, `timestamp-nanos` | `datetime` |
| `local-timestamp-millis`, `local-timestamp-micros`, `local-timestamp-nanos` | local date-time format |
| `duration` | `timedelta` |

Generated temporal defaults use Avro's day, millisecond, microsecond, or nanosecond units. Timestamp defaults use UTC; local timestamp and time defaults have no timezone. Backends that map these formats to strings receive ISO strings. Defaults outside Python's date/time range, or nanosecond defaults that cannot be represented exactly at microsecond precision, produce an error.

Decimal defaults decode the bytes or fixed value as a signed big-endian integer and apply the declared scale exactly. Generated `Decimal` values preserve trailing zeros and do not depend on the active decimal context's precision. A scale outside Python's Decimal range produces an error.

Duration defaults with zero months and zero days preserve the milliseconds component as `timedelta`. Nonzero calendar components produce an error because their meaning depends on the calendar and cannot be represented exactly by `timedelta`. Encoded duration defaults must contain exactly 12 bytes.

Avro-specific metadata is preserved in generated JSON Schema extensions such as `x-avro-fullname`, `x-avro-namespace`, `x-avro-aliases`, and `x-avro-logicalType` before model generation.

## Schema Version

Apache Avro schemas do not include an in-schema version marker equivalent to JSON Schema's `$schema`, OpenAPI's `openapi`, or XML Schema versioning attributes. For that reason, `--schema-version` does not select an Avro specification version. The Avro parser follows the currently implemented Apache Avro schema rules and logical types.

## Limitations

datamodel-code-generator uses Avro schemas to generate Python model definitions. It does not implement Avro runtime validation, binary encoding, decoding, or serialization.

## 📖 See Also

- 🖥️ [CLI Reference](cli-reference/index.md) - Complete CLI options reference
- 📊 [Supported Data Types](supported-data-types.md) - Data type support details
- 📋 [Generate from JSON Schema](jsonschema.md) - JSON Schema input documentation
