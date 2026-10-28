# 📚 Supported Input Formats

This code generator supports the following input formats:

- OpenAPI 3.0/3.1/3.2 (YAML/JSON, [OpenAPI Data Types](https://spec.openapis.org/oas/v3.2.0.html#data-types)); OpenAPI 2.0 (Swagger) has limited support.
- [AsyncAPI](asyncapi.md) 2.x/3.x (YAML/JSON).
- JSON Schema ([JSON Schema Core](https://json-schema.org/draft/2020-12/json-schema-core.html) / [JSON Schema Validation](https://json-schema.org/draft/2020-12/json-schema-validation.html)).
- Apache Avro schema (`.avsc`, [Apache Avro](avro.md)).
- [XML Schema](xmlschema.md) (XSD).
- Protocol Buffers / gRPC (`.proto`, [Protocol Buffers / gRPC](protobuf.md)).
- GraphQL schema ([GraphQL Schemas and Types](https://graphql.org/learn/schema/)).
- MCP tool schemas (`--input-file-type mcp-tools`, [MCP Tool Schemas](mcp-tools.md)).
- JSON / YAML / CSV data (converted to JSON Schema before model generation).
- Python dictionary (converted to JSON Schema before model generation).
- Existing Python types via [`--input-model`](python-model.md): Pydantic models, dataclasses, Pydantic dataclasses, TypedDict, and dict schemas.

Use `--input-file-type auto` (the default) for common files, or set an explicit
type when a file extension is ambiguous. For example, YAML can contain either an
OpenAPI/JSON Schema document or raw sample data, so use `jsonschema`, `openapi`,
or `yaml` depending on the intended input.

## Input Format Guide

<!-- BEGIN AUTO-GENERATED INPUT FORMAT GUIDE -->
### Input Format Guide (from code)

The tables below are generated from the input type enum, parser routing code, schema version enums, raw-data conversion rules, and parser type conversion maps.

#### Parser Routes and Version Flags

| Input format | Selector | Parser route | `--schema-version` | Auto detection |
| --- | --- | --- | --- | --- |
| JSON Schema | `jsonschema` | `JsonSchemaParser` | `draft-04`, `draft-06`, `draft-07`, `2019-09`, `2020-12`, `auto` | `$schema`, `type`, `properties`, or composition keywords |
| OpenAPI | `openapi` | `OpenAPIParser` | `3.0`, `3.1`, `3.2`, `auto` | `openapi` field |
| AsyncAPI | `asyncapi` | `AsyncAPIParser` | `2.0`, `3.0`, `auto` | `asyncapi` field |
| GraphQL schema | `graphql` | `GraphQLParser` | Not supported | Explicit only |
| XML Schema | `xmlschema` | `XMLSchemaParser` | `1.0`, `1.1`, `auto` | XML Schema namespace on the root element |
| Protocol Buffers | `protobuf` | `ProtobufParser` | `proto2`, `proto3`, `2023`, `auto` | Protocol Buffers syntax/message-like text |
| Apache Avro | `avro` | `AvroParser` | Not supported | Avro schema object, union, or primitive schema form |
| MCP tool schemas | `mcp-tools` | `JsonSchemaParser after MCP conversion` | Converted to JSON Schema; `draft-04`, `draft-06`, `draft-07`, `2019-09`, `2020-12`, `auto` | Explicit only |
| JSON data | `json` | `JsonSchemaParser after genson conversion` | Converted to JSON Schema; `draft-04`, `draft-06`, `draft-07`, `2019-09`, `2020-12`, `auto` | Mapping that is not a schema/OpenAPI/AsyncAPI/Avro document |
| YAML data | `yaml` | `JsonSchemaParser after genson conversion` | Converted to JSON Schema; `draft-04`, `draft-06`, `draft-07`, `2019-09`, `2020-12`, `auto` | Explicit for YAML sample data |
| CSV data | `csv` | `JsonSchemaParser after genson conversion` | Converted to JSON Schema; `draft-04`, `draft-06`, `draft-07`, `2019-09`, `2020-12`, `auto` | Detected from CSV-like text with consistent comma counts across non-empty lines or explicit `csv` |
| Python dictionary data | `dict` | `JsonSchemaParser after genson conversion` | Converted to JSON Schema; `draft-04`, `draft-06`, `draft-07`, `2019-09`, `2020-12`, `auto` | Explicit for mapping input |
| Python input model | `--input-model` | `JsonSchemaParser` after Python schema conversion | JSON Schema after conversion; dict input can select another explicit schema type | Explicit only |

#### Accepted Source Shapes

| Input format | Accepted source shape |
| --- | --- |
| JSON Schema | JSON Schema document as JSON/YAML, mapping, URL, or file |
| OpenAPI | OpenAPI document as JSON/YAML, mapping, URL, or file |
| AsyncAPI | AsyncAPI document as JSON/YAML, mapping, URL, or file |
| GraphQL schema | GraphQL SDL text, URL, or file |
| XML Schema | XSD XML text, URL, or file |
| Protocol Buffers | .proto text, file, directory, URL, or path list |
| Apache Avro | Avro schema JSON/YAML, mapping, list, URL, or file |
| MCP tool schemas | MCP tool list/profile as JSON/YAML, mapping, list, URL, or file |
| JSON data | JSON sample data text or file |
| YAML data | YAML sample data text or file |
| CSV data | CSV text or file with a header row and at least one data row |
| Python dictionary data | In-memory mapping or Python literal data |
| Python input model | `module:Object` or `path/to/file.py:Object` via `--input-model` |

#### Format Type Coverage

| Input family | Code-derived coverage | Guide |
| --- | --- | --- |
| JSON Schema | 7 schema types, 54 common formats | Uses JSON Schema type/format mappings and feature metadata below |
| OpenAPI | JSON Schema mappings plus 2 OpenAPI-only formats | Adds OpenAPI-specific schema features and formats |
| GraphQL | 6 GraphQL type kinds | Uses GraphQL SDL parser order and parser method map |
| XML Schema | 50 built-in XSD datatypes | Converts XSD built-ins to JSON Schema fragments |
| Protocol Buffers | 15 scalar field types, 17 well-known type mappings | Converts descriptors to JSON Schema definitions |
| Apache Avro | 8 primitives, 13 logical type mappings | Converts Avro schemas to JSON Schema while preserving Avro metadata |
| JSON/YAML/CSV/Dict data | 4 raw data selectors | Samples are converted to JSON Schema with genson before parsing |
| Python input model | 4 supported object kinds | Python objects are converted to schema data before normal generation |

#### GraphQL Type Kinds

| GraphQL kind | Parser method | Generated shape |
| --- | --- | --- |
| `scalar` | `parse_scalar` | Generates scalar aliases |
| `enum` | `parse_enum` | Generates enums or literals depending on enum options |
| `interface` | `parse_interface` | Generates model classes from interface fields |
| `object` | `parse_object` | Generates model classes except root Query/Mutation objects |
| `input_object` | `parse_input_object` | Generates model classes for input objects |
| `union` | `parse_union` | Generates union type aliases |

#### Apache Avro Primitive Types

| Avro primitive | JSON Schema mapping | Constraints |
| --- | --- | --- |
| `null` | `null` | - |
| `boolean` | `boolean` | - |
| `int` | `integer` + `int32` | - |
| `long` | `integer` + `int64` | - |
| `float` | `number` + `float` | - |
| `double` | `number` + `double` | - |
| `bytes` | `string` + `binary` | - |
| `string` | `string` | - |

#### Apache Avro Logical Types

| Avro logical type | Allowed Avro type | JSON Schema mapping |
| --- | --- | --- |
| `decimal` | `bytes`, `fixed` | `string` + `decimal` |
| `big-decimal` | `bytes` | `string` + `decimal` |
| `uuid` | `string`, `fixed` | `string` + `uuid` |
| `date` | `int` | `string` + `date` |
| `time-millis` | `int` | `string` + `time` |
| `time-micros` | `long` | `string` + `time` |
| `timestamp-millis` | `long` | `string` + `date-time` |
| `timestamp-micros` | `long` | `string` + `date-time` |
| `timestamp-nanos` | `long` | `string` + `date-time` |
| `local-timestamp-millis` | `long` | `string` + `date-time-local` |
| `local-timestamp-micros` | `long` | `string` + `date-time-local` |
| `local-timestamp-nanos` | `long` | `string` + `date-time-local` |
| `duration` | `fixed` | `string` + `duration` |

#### Protocol Buffers Scalar Types

| Protobuf scalar | JSON Schema mapping | Constraints |
| --- | --- | --- |
| `double` | `number` + `double` | - |
| `float` | `number` + `float` | - |
| `int64` | `integer` + `int64` | - |
| `uint64` | `integer` + `int64` | minimum=0, maximum=18446744073709551615 |
| `int32` | `integer` + `int32` | - |
| `uint32` | `integer` + `int32` | minimum=0, maximum=4294967295 |
| `sint32` | `integer` + `int32` | - |
| `sint64` | `integer` + `int64` | - |
| `fixed32` | `integer` + `int32` | minimum=0, maximum=4294967295 |
| `fixed64` | `integer` + `int64` | minimum=0, maximum=18446744073709551615 |
| `sfixed32` | `integer` + `int32` | - |
| `sfixed64` | `integer` + `int64` | - |
| `bool` | `boolean` | - |
| `string` | `string` | - |
| `bytes` | `string` + `binary` | - |

#### Protocol Buffers Well-Known Types

| Well-known type | JSON Schema mapping |
| --- | --- |
| `google.protobuf.Timestamp` | `string` + `date-time` |
| `google.protobuf.Duration` | `string` + `duration` |
| `google.protobuf.Struct` | `object` map |
| `google.protobuf.ListValue` | `array` |
| `google.protobuf.Value` | `null` \| `boolean` \| `number` \| `string` \| `object` map \| `array` |
| `google.protobuf.Any` | `object` map |
| `google.protobuf.Empty` | `object` |
| `google.protobuf.FieldMask` | `string` |
| `google.protobuf.DoubleValue` | `number` + `double` \| `null` |
| `google.protobuf.FloatValue` | `number` + `float` \| `null` |
| `google.protobuf.Int64Value` | `integer` + `int64` \| `null` |
| `google.protobuf.UInt64Value` | `integer` + `int64` \| `null` |
| `google.protobuf.Int32Value` | `integer` + `int32` \| `null` |
| `google.protobuf.UInt32Value` | `integer` + `int32` \| `null` |
| `google.protobuf.BoolValue` | `boolean` \| `null` |
| `google.protobuf.StringValue` | `string` \| `null` |
| `google.protobuf.BytesValue` | `string` + `binary` \| `null` |

#### XML Schema Built-In Type Groups

| JSON Schema mapping | XSD built-ins | Count |
| --- | --- | --- |
| `Any` | `anySimpleType`, `anyAtomicType`, `anyType` | 3 |
| `string` + `uri` | `anyURI` | 1 |
| `string` + `byte` | `base64Binary` | 1 |
| `boolean` | `boolean` | 1 |
| `integer` | `byte`, `int`, `integer`, `long`, `negativeInteger`, `nonNegativeInteger`, `nonPositiveInteger`, `positiveInteger`, +5 more | 13 |
| `string` + `date` | `date` | 1 |
| `string` + `date-time` | `dateTime`, `dateTimeStamp` | 2 |
| `number` + `decimal` | `decimal` | 1 |
| `number` | `double`, `float` | 2 |
| `string` | `duration`, `ENTITY`, `gDay`, `gMonth`, `gMonthDay`, `gYear`, `gYearMonth`, `hexBinary`, +12 more | 20 |
| `string` + `duration` | `dayTimeDuration` | 1 |
| `array` | `ENTITIES`, `IDREFS`, `NMTOKENS` | 3 |
| `string` + `time` | `time` | 1 |

#### Python Input Types

| Python input object | Conversion behavior |
| --- | --- |
| `dict` | Returned directly as the schema; `--input-file-type` is required |
| `Pydantic v2 BaseModel` | Converted with Pydantic `model_json_schema()` |
| `dataclass` | Converted with Pydantic `TypeAdapter`; includes stdlib and Pydantic dataclasses |
| `TypedDict` | Converted with Pydantic `TypeAdapter` |
| Multiple `--input-model` | Supported only for Pydantic v2 BaseModel classes |
<!-- END AUTO-GENERATED INPUT FORMAT GUIDE -->

## 📘 OpenAPI 3 and JSON Schema {#openapi-3-and-json-schema}

Below are the data types and features recognized by datamodel-code-generator for OpenAPI 3 and JSON Schema.

<!-- BEGIN AUTO-GENERATED SCHEMA FEATURE SUPPORT -->
### Schema Feature Support (from code)

The JSON Schema and OpenAPI feature matrix below is generated from the same code metadata used by the schema version support page.

#### JSON Schema Features

| Feature | Introduced | Status | Description |
|---------|------------|--------|-------------|
| `Null in type array` | Draft 4 or earlier | ✅ Supported | Allows `type: ['string', 'null']` syntax for nullable types |
| `$defs` | 2019-09 | ✅ Supported | Uses `$defs` instead of `definitions` for schema definitions |
| `prefixItems` | 2020-12 | ✅ Supported | Tuple validation using `prefixItems` keyword |
| `Boolean schemas` | Draft 6 | ✅ Supported | Allows `true` and `false` as valid schemas |
| `$id` | Draft 6 | ✅ Supported | Schema identifier field (`id` in Draft 4, `$id` in Draft 6+) |
| `definitions/$defs` | Draft 4 | ✅ Supported | Key for reusable schema definitions |
| `exclusiveMinimum/Maximum as number` | Draft 6 | ✅ Supported | Numeric `exclusiveMinimum`/`exclusiveMaximum` (boolean in Draft 4) |
| `readOnly/writeOnly` | Draft 7 | ✅ Supported | Field visibility hints for read-only and write-only properties |
| `const` | Draft 6 | ✅ Supported | Single constant value constraint |
| `propertyNames` | Draft 6 | ✅ Supported | Dict key type constraints via pattern, enum, or $ref |
| `contains` | Draft 6 | ⚠️ Partial | Count constraints are modeled when contains matches every item; general schema-valued contains is not supported |
| `deprecated` | 2019-09 | ⚠️ Partial | Marks schema elements as deprecated |
| `if/then/else` | Draft 7 | ❌ Not Supported | Conditional schema validation |
| `contentMediaType/contentEncoding` | Draft 7 | ⚠️ Partial | Content type and encoding hints for strings |
| `contentSchema` | 2019-09 | ⚠️ Partial | Schema for decoded string content |
| `$anchor` | 2019-09 | ✅ Supported | Location-independent schema references |
| `$vocabulary` | 2019-09 | ❌ Not Supported | Vocabulary declarations for meta-schemas |
| `unevaluatedProperties` | 2019-09 | ⚠️ Partial | Additional properties not evaluated by subschemas |
| `unevaluatedItems` | 2019-09 | ⚠️ Partial | Additional items not evaluated by subschemas |
| `dependentRequired` | 2019-09 | ❌ Not Supported | Conditional property requirements |
| `dependentSchemas` | 2019-09 | ❌ Not Supported | Conditional schema application based on property presence |
| `$recursiveRef/$recursiveAnchor` | 2019-09 | ✅ Supported | Recursive reference resolution via anchors |
| `$dynamicRef/$dynamicAnchor` | 2020-12 | ✅ Supported | Dynamic reference resolution across schemas |

#### OpenAPI-Specific Features

| Feature | Introduced | Status | Description |
|---------|------------|--------|-------------|
| `nullable` | OAS 3.0 | ✅ Supported | Uses `nullable: true` for nullable types (deprecated in 3.1) |
| `discriminator` | OAS 3.0 | ✅ Supported | Polymorphism support via `discriminator` keyword |
| `webhooks` | OAS 3.1 | ✅ Supported | Top-level webhooks object for incoming events |
| `$ref with sibling keywords` | OAS 3.1 | ⚠️ Partial | $ref can coexist with description, summary (no allOf workaround) |
| `itemSchema` | OAS 3.2 | ✅ Supported | Media Type Object item schema for sequential media |
| `$self` | OAS 3.2 | ✅ Supported | Root document URI for relative and absolute reference resolution |
| `querystring` | OAS 3.2 | ✅ Supported | Query string parameter object without a parameter name |
| `xml` | OAS 3.0 | ⚠️ Partial | XML serialization metadata (name, namespace, prefix) |
| `externalDocs` | OAS 3.0 | ⚠️ Partial | Reference to external documentation |
| `links` | OAS 3.0 | ❌ Not Supported | Links between operations |
| `callbacks` | OAS 3.0 | ❌ Not Supported | Callback definitions for webhooks |
| `securitySchemes` | OAS 3.0 | ❌ Not Supported | API security mechanism definitions |
<!-- END AUTO-GENERATED SCHEMA FEATURE SUPPORT -->

## ✅ Implemented data types and features

<!-- BEGIN AUTO-GENERATED DATA TYPE SUPPORT -->
### Implemented Data Types and Formats

The schema type, format, and default Pydantic v2 type columns below are generated from the parser's schema format registry and the default Pydantic v2 type mapping; keyword and note columns document supplemental schema details.

#### Schema Data Types

| Schema Type | Default Pydantic v2 Type | Supported Keywords |
|-------------|--------------------------|--------------------|
| `integer` | `int` | maximum, exclusiveMaximum, minimum, exclusiveMinimum, multipleOf |
| `number` | `float` | maximum, exclusiveMaximum, minimum, exclusiveMinimum, multipleOf |
| `string` | `str` | pattern, minLength, maxLength |
| `boolean` | `bool` | - |
| `object` | `Dict[str, Any]` | properties, required, additionalProperties, patternProperties |
| `null` | `None` | - |
| `array` | `List[Any]` | items, prefixItems, minItems, maxItems, uniqueItems |

#### Common Formats (JSON Schema + OpenAPI)

| Schema Type | Format | Default Pydantic v2 Type | Notes |
|-------------|--------|--------------------------|-------|
| `integer` | `default` (no `format`) | `int` | - |
| `integer` | `int32` | `int` | - |
| `integer` | `int64` | `int` | - |
| `integer` | `date-time` | `AwareDatetime` | - |
| `integer` | `unix-time` | `int` | - |
| `integer` | `unixtime` | `int` | - |
| `number` | `default` (no `format`) | `float` | - |
| `number` | `float` | `float` | - |
| `number` | `double` | `float` | - |
| `number` | `decimal` | `Decimal` | - |
| `number` | `date-time` | `AwareDatetime` | - |
| `number` | `time` | `time` | - |
| `number` | `time-delta` | `timedelta` | - |
| `number` | `unixtime` | `int` | - |
| `string` | `default` (no `format`) | `str` | - |
| `string` | `byte` | `Base64Str` | Base64 encoded string |
| `string` | `date` | `date` | - |
| `string` | `date-time` | `AwareDatetime` | - |
| `string` | `timestamp with time zone` | `AwareDatetime` | - |
| `string` | `date-time-local` | `NaiveDatetime` | - |
| `string` | `duration` | `timedelta` | - |
| `string` | `time` | `time` | - |
| `string` | `time-local` | `time` | - |
| `string` | `path` | `Path` | - |
| `string` | `email` | `EmailStr` | Requires email-validator |
| `string` | `idn-email` | `EmailStr` | Requires email-validator |
| `string` | `idn-hostname` | `str` | - |
| `string` | `iri` | `str` | - |
| `string` | `iri-reference` | `str` | - |
| `string` | `uuid` | `UUID` | - |
| `string` | `uuid1` | `UUID1` | - |
| `string` | `uuid2` | `UUID` | - |
| `string` | `uuid3` | `UUID3` | - |
| `string` | `uuid4` | `UUID4` | - |
| `string` | `uuid5` | `UUID5` | - |
| `string` | `uri` | `AnyUrl` | - |
| `string` | `uri-reference` | `str` | - |
| `string` | `uri-template` | `str` | - |
| `string` | `json-pointer` | `str` | - |
| `string` | `relative-json-pointer` | `str` | - |
| `string` | `regex` | `str` | - |
| `string` | `hostname` | `str` | - |
| `string` | `ipv4` | `IPv4Address` | - |
| `string` | `ipv4-network` | `IPv4Network` | - |
| `string` | `ipv6` | `IPv6Address` | - |
| `string` | `ipv6-network` | `IPv6Network` | - |
| `string` | `decimal` | `Decimal` | - |
| `string` | `integer` | `int` | - |
| `string` | `unixtime` | `int` | - |
| `string` | `ulid` | `ULID` | Requires python-ulid |
| `boolean` | `default` (no `format`) | `bool` | - |
| `object` | `default` (no `format`) | `Dict[str, Any]` | - |
| `null` | `default` (no `format`) | `None` | - |
| `array` | `default` (no `format`) | `List[Any]` | - |

#### OpenAPI-Only Formats

| Schema Type | Format | Default Pydantic v2 Type | Notes |
|-------------|--------|--------------------------|-------|
| `string` | `binary` | `bytes` | File content |
| `string` | `password` | `SecretStr` | - |
<!-- END AUTO-GENERATED DATA TYPE SUPPORT -->

### 🔗 Other schema
- enum (as enum.Enum or typing.Literal)
- allOf (as Multiple inheritance)
- anyOf (as typing.Union)
- oneOf (as typing.Union)
- $ref ([http extra](index.md#http-extra-option) is required when resolving $ref for remote files.)
- $id (for [JSONSchema](https://json-schema.org/understanding-json-schema/structuring.html#id))
