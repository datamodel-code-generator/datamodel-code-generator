# 📡 Generate from AsyncAPI

Generate Python models from AsyncAPI 2.x and 3.x documents.

!!! warning "Experimental"
    AsyncAPI input support is experimental and may change as real-world usage is validated.

## 🚀 Quick Start

```bash
datamodel-codegen \
    --input asyncapi.yaml \
    --input-file-type asyncapi \
    --output-model-type pydantic_v2.BaseModel \
    --output model.py
```

## 📝 Example

**asyncapi.yaml**
```yaml
asyncapi: 3.0.0
info:
  title: User events
  version: 1.0.0
channels:
  userSignedUp:
    messages:
      userSignedUp:
        payload:
          $ref: '#/components/schemas/UserSignedUp'
components:
  schemas:
    UserSignedUp:
      type: object
      required:
        - id
        - email
      properties:
        id:
          type: string
        email:
          type: string
          format: email
```

**✨ Generated model.py**
```python
from __future__ import annotations

from pydantic import BaseModel, EmailStr


class UserSignedUp(BaseModel):
    id: str
    email: EmailStr
```

## Supported AsyncAPI Versions

The AsyncAPI parser supports documents with `asyncapi` versions in the 2.x and 3.x families.

| Version | Schema behavior |
|---------|-----------------|
| 2.x | Message `schemaFormat` is applied to the message payload. AsyncAPI Schema Objects are handled with OpenAPI 3.0-compatible schema features. |
| 3.x | `payload`, `headers`, and `components.schemas` may be AsyncAPI Schema Objects, Reference Objects, or Multi Format Schema Objects. AsyncAPI Schema Objects are handled with OpenAPI 3.1-compatible schema features. |

Use `--schema-version auto` to detect the version from the `asyncapi` field, or pass
`--schema-version 2.0` / `--schema-version 3.0` to select a version explicitly.

## Model Generation Scope

`--openapi-scopes` does not select what AsyncAPI input generates: the parser generates models from every location listed below. The `api` scope is available for OpenAPI input only, and AsyncAPI input with `api` stops with an error.

datamodel-code-generator generates data models from schema-bearing fields. It does not generate servers, protocol adapters, or runtime message validators; it generates a WebSocket client for the channels of `ws` and `wss` servers (see [Generate a client](#generate-a-client)).

The AsyncAPI parser extracts schemas from:

| AsyncAPI location | Generated models |
|-------------------|------------------|
| `components.schemas` | Reusable schema models |
| `components.messages[*].payload` and `headers` | Reusable message payload/header models |
| `components.messages[*].bindings` | Binding schemas for schema-bearing binding fields such as Kafka `key` and HTTP `headers` |
| `channels[*].publish.message`, `subscribe.message`, and `messages[*]` | Channel message payload/header models |
| `channels[*].parameters[*].schema` and `components.parameters[*].schema` | Channel parameter schema models |
| `channels[*].bindings` and `components.channels[*].bindings` | Binding schemas for schema-bearing binding fields such as WebSockets `query`/`headers` |
| `operations[*].messages` and `operations[*].reply.messages` | Operation request/reply message payload/header models |
| `components.operations[*].messages` and `reply.messages` | Reusable operation message payload/header models |
| `operations[*].bindings` and `components.operations[*].bindings` | Binding schemas for schema-bearing binding fields such as HTTP `query` and Kafka `groupId`/`clientId` |
| `components.replies[*].messages` | Reusable reply message payload/header models |
| `message.traits[*].headers` and `components.messageTraits[*].headers` | Header models when the message itself does not define `headers` |
| `message.traits[*].bindings` and `components.messageTraits[*].bindings` | Binding schemas supplied by message traits |
| `operation.traits[*].bindings` and `components.operationTraits[*].bindings` | Binding schemas supplied by operation traits |
| Local and external Reference Objects | Resolved before model generation |

Protocol binding configuration is treated as transport metadata unless the AsyncAPI binding
specification defines the field as a Schema Object or Reference Object. The parser currently
generates models for the schema-bearing binding fields used by the official bindings, including
`headers`, `query`, `key`, `groupId`, and `clientId`.

## Schema Formats

AsyncAPI 3.x Multi Format Schema Objects are unwrapped through `schemaFormat` before model generation.
AsyncAPI 2.x message-level `schemaFormat` is applied to the message payload.

Supported embedded schema formats are:

| `schemaFormat` family | Behavior |
|-----------------------|----------|
| `application/vnd.aai.asyncapi...` or omitted | Parsed as an AsyncAPI Schema Object |
| `application/schema+json` / `application/schema+yaml` | Parsed with the JSON Schema/OpenAPI schema stack |
| `application/vnd.oai.openapi...` | Parsed with the OpenAPI schema stack |
| `application/vnd.apache.avro...` | Converted with the Avro parser, then generated as Python models |
| `application/vnd.google.protobuf` | Converted with the Protocol Buffers parser, then generated as Python models |
| `application/xml`, `text/xml`, `application/xsd+xml`, `application/xml+schema`, `application/xml-schema` | Converted with the XML Schema parser, then generated as Python models |

RAML and custom embedded `schemaFormat` values inside an AsyncAPI document are rejected with an explicit error instead of producing partial or misleading models.

Embedded Protocol Buffers schemas support inline `.proto` source strings and local imports resolved relative to the AsyncAPI document. Embedded XML Schema supports inline XSD strings and local `xs:include` / `xs:import` / `xs:redefine` / `xs:override` locations resolved relative to the AsyncAPI document.

## Limitations

The AsyncAPI input type is for model generation from message payloads, headers, and reusable schemas. It does not:

- validate complete AsyncAPI documents;
- apply protocol binding runtime semantics;
- generate producer/consumer code;
- enforce runtime message validation;
- merge multiple traits that define the same `headers` property.

## 📖 See Also

- 🖥️ [CLI Reference](cli-reference/index.md) - Complete CLI options reference
- 📋 [Generate from JSON Schema](jsonschema.md) - JSON Schema input documentation
- 📘 [Generate from OpenAPI](openapi.md) - OpenAPI schema behavior
- 🪶 [Generate from Apache Avro Schema](avro.md) - Avro schema input documentation

## Generate a client

!!! warning "Experimental"
    Client generation from AsyncAPI documents is experimental (`behavior.asyncapi-client`); the channels it opens and
    the generated package may change.

`--generate-client httpx2` with `--input-file-type asyncapi` generates the models and an HTTPX2 client package whose
attributes open the WebSocket channels of the document, sync and asyncio:

```bash
datamodel-codegen --input market.yaml --input-file-type asyncapi --output models.py \
  --generate-client httpx2 --client-output client --client-package client --client-model-package models
```

```python
from client import Client
from models import Subscribe

with Client() as client, client.market.connect(pair="XBT-USD") as session:
    session.send(Subscribe(event="subscribe", channels=["trades"]))
    for message in session:
        print(message.data)
```

- Each channel of AsyncAPI 2.x or 3.x that a `ws` or `wss` server serves becomes `client.<channel>`, named after the
  words of its id (AsyncAPI 3) or its address (AsyncAPI 2). Its `connect(...)` takes the parameters of the address as
  keyword arguments and returns the session of a [WebSocket helper](python-client-helpers.md): it sends the channel's
  handshake, a GET of its address on its servers (with `wss` and `ws` sent as `https` and `http`), through the client's
  call path, and sends and receives typed messages until the block ends. An address is a path under the server URL,
  with or without its leading `/`. The channels of a document that declares no servers are opened at
  `--client-default-base-url`, which they need as OpenAPI operations without servers do, or at the `base_url` the
  client is given.
- The models are the only codec: a JSON message is encoded and decoded by its payload's model, a payload that is only
  a `$ref` by the model of the schema it names, a `text/plain` string payload as `str`, and an
  `application/octet-stream` or binary payload as `bytes`. A string with a `contentEncoding` in a media type other
  than JSON or `application/octet-stream`, such as base64 text, is not coded. A direction that carries no message is
  typed `Never`.
- A direction of several messages is typed as the union of their types. A received binary frame is the bytes message
  and a text frame the text or JSON message; several JSON messages are told apart by a tag, a property that every
  payload model declares as required with one string value of its own (a `const` or a one-value `enum`), and a message
  with an unknown or missing tag raises `DecodeError` and closes the connection with 1002. `send(value)` takes the
  message of the value's class, a `str` the text message and `bytes` the binary one; TypedDict values take the message
  whose tag they hold. Pydantic models decode received JSON messages without a tag, and encode sent ones that are not
  all classes, as the union of their types; for the other model types such a channel is left out with a warning, as is
  a channel whose client receives both text and JSON messages.
- [`--client-asyncapi-perspective`](cli-reference/target-generation-options.md#client-asyncapi-perspective) says which
  side the document describes: `server` (the default), whose sends the client receives, or `client`.
- `--client-operations` keyed by a channel's reference, such as `/channels/market`, renames its attribute (`resource`,
  one identifier that no other channel takes) and its parameters (`parameter_names`); `--client-protocols` is refused
  for AsyncAPI input.

A channel that uses a feature the client does not support is left out with a warning that names the feature, and the
other channels are generated; when every channel is left out, the generation fails with their findings. A channel is
also left out, with a warning that names what does not resolve, when an operation, a message or a trait it needs is
in another document, or its payload schema is in no loaded document (such as one `--external-ref-mapping` maps); an
operation in another document may be on any channel, so it leaves every channel out. Operation and message traits are
merged first, as the document's AsyncAPI version merges them. [AsyncAPI Client Features](supported_formats.md#asyncapi-client-features) lists each
feature and the client's support.
