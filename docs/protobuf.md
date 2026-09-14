# 🧩 Generate from Protocol Buffers / gRPC

Generate Python models from Protocol Buffers schema files (`.proto`), including message types referenced by gRPC service definitions.

!!! warning "Experimental"
    Protocol Buffers input support is experimental and may change as real-world usage is validated.

## 🚀 Quick Start

Install the Protocol Buffers parser extra:

```bash
pip install 'datamodel-code-generator[protobuf]'
```

Generate models from a `.proto` file:

```bash
datamodel-codegen \
    --input order.proto \
    --input-file-type protobuf \
    --output-model-type pydantic_v2.BaseModel \
    --output model.py
```

## 📝 Example

**order.proto**
```proto
syntax = "proto3";

package example.shop.v1;

import "google/protobuf/timestamp.proto";

message Order {
  // Unique order identifier.
  string id = 1;
  repeated string tags = 2;
  google.protobuf.Timestamp created_at = 3;

  oneof contact {
    string email = 4;
    string phone = 5;
  }
}

message GetOrderRequest {
  string id = 1;
}

message GetOrderResponse {
  Order order = 1;
}

service OrderService {
  rpc GetOrder(GetOrderRequest) returns (GetOrderResponse);
}
```

**✨ Generated model.py**
```python
from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, Field


class ExampleShopV1Order(BaseModel):
    id: str | None = Field("", description="Unique order identifier.")
    tags: list[str] | None = []
    created_at: AwareDatetime | None = None
    email: str | None = Field(None, description="oneof: contact")
    phone: str | None = Field(None, description="oneof: contact")


class ExampleShopV1GetOrderRequest(BaseModel):
    id: str | None = ""


class ExampleShopV1GetOrderResponse(BaseModel):
    order: ExampleShopV1Order | None = None
```

## Supported Protocol Buffers Features

The Protocol Buffers parser supports the schema constructs needed to generate Python model definitions:

| Protobuf construct | Model generation behavior |
|--------------------|---------------------------|
| Scalar fields | Maps protobuf scalar types to Python scalar types |
| `message` | Generates a Python model class |
| Nested `message` | Generates a named Python model class with package and parent context |
| `enum` | Generates an enum class |
| `repeated` | Generates a list field |
| `map<K, T>` | Generates a dict field for protobuf-supported string, integer, and bool key types |
| `oneof` | Generates nullable fields and preserves oneof membership as field metadata |
| `optional`, `required`, singular | Preserves proto2/proto3 presence and requiredness where model generation can represent it |
| Defaults | Preserves explicit proto2 defaults and proto3 implicit defaults |
| Package names | Included in generated class names to avoid cross-file collisions |
| Imports | Resolves regular imports, public imports, weak imports, fully-qualified type references, and cross-file references |
| Services and RPCs | Keeps request and response message types reachable for unary and streaming RPCs |
| Comments | Preserved as field descriptions when field descriptions are enabled |

Supported scalar types are `double`, `float`, `int32`, `int64`, `uint32`, `uint64`, `sint32`, `sint64`, `fixed32`, `fixed64`, `sfixed32`, `sfixed64`, `bool`, `string`, and `bytes`.

## Well-Known Types

Common `google.protobuf` well-known types are mapped through the generator's normal type handling:

| Protobuf type | Typical generated Python type |
|---------------|-------------------------------|
| `Timestamp` | `AwareDatetime` |
| `Duration` | `timedelta` |
| `Struct` | `dict[str, Any]` |
| `Value` | `bool | float | str | dict[str, Any] | list[Any] | None` |
| `ListValue` | `list[Any]` |
| `Any` | `dict[str, Any]` |
| `FieldMask` | `str` |
| Wrapper types | Optional scalar fields |

Referenced standard types without a built-in mapping are generated from their
compiled descriptors; unrelated standard types remain omitted. This includes the
`NullValue` enum, represented by its `NULL_VALUE` member like other generated enums.
This enum representation does not implement ProtoJSON's special `null` serialization;
acceptance of `None` follows the selected output backend.

## Schema Version

Protocol Buffers syntax is detected from each compiled `.proto` descriptor:

- `syntax = "proto3";` -> proto3
- `syntax = "proto2";` or no syntax declaration -> proto2
- `edition = "2023";` -> edition 2023

Use `--schema-version proto2`, `--schema-version proto3`, or `--schema-version 2023` to override
auto-detection when needed.

## Multiple Files

Pass a directory as input to generate from a group of related `.proto` files:

```bash
datamodel-codegen \
    --input ./protos \
    --input-file-type protobuf \
    --output ./models \
    --module-split-mode single
```

The parser resolves imports relative to the input file or directory and emits importable Python modules when an output directory is used.

## Limitations

datamodel-code-generator uses Protocol Buffers schemas to generate Python model definitions. It does not implement protobuf runtime validation, oneof runtime exclusivity checks, wire serialization, or gRPC client/server code generation.

Some protobuf constructs are parsed only as needed for data model generation. Options, custom options, extensions, reserved declarations, and service streaming markers are tolerated where possible but are not emitted as runtime protobuf behavior.

## 📖 See Also

- 🖥️ [CLI Reference](cli-reference/index.md) - Complete CLI options reference
- 📊 [Supported Data Types](supported-data-types.md) - Data type support details
- 📋 [Generate from JSON Schema](jsonschema.md) - JSON Schema input documentation
