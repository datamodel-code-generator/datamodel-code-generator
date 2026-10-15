# 🧾 Generate from XML Schema

Generate Python models from W3C XML Schema documents (`.xsd`).

!!! warning "Experimental"
    XML Schema input support is experimental and may change as real-world usage is validated.

## 🚀 Quick Start

```bash
datamodel-codegen \
    --input purchase_order.xsd \
    --input-file-type xmlschema \
    --output-model-type pydantic_v2.BaseModel \
    --output model.py
```

## 📝 Example

**purchase_order.xsd**
```xml
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="purchaseOrder" type="PurchaseOrder"/>

  <xs:complexType name="PurchaseOrder">
    <xs:sequence>
      <xs:element name="id" type="xs:string"/>
      <xs:element name="total" type="xs:decimal"/>
    </xs:sequence>
  </xs:complexType>
</xs:schema>
```

**✨ Generated model.py**
```python
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class PurchaseOrder(BaseModel):
    id: str
    total: Decimal
```

## Supported XML Schema Versions

The XML Schema parser supports XML Schema 1.0 and selected XML Schema 1.1 constructs.
Use `--schema-version auto` to detect the version from versioning attributes and XSD
1.1 constructs, or pass `--schema-version 1.0` / `--schema-version 1.1` explicitly.

## Supported XML Schema Features

The XML Schema parser converts XSD into the JSON Schema shape used by the normal
model-generation pipeline. It supports the constructs needed to generate Python
model definitions:

| XSD construct | Model generation behavior |
|---------------|---------------------------|
| Built-in simple types | Maps XML Schema scalar types to Python scalar types |
| `xs:complexType` | Generates a Python model class |
| `xs:simpleType` restrictions | Generates constrained scalar fields where possible |
| `xs:sequence`, `xs:choice`, `xs:all` | Generates fields from model particles |
| `minOccurs`, `maxOccurs`, `nillable` | Preserves optionality, lists, and nullability |
| Attributes | Generates model fields for XML attributes |
| `xs:include`, `xs:import`, `xs:redefine`, `xs:override` | Resolves local schema composition |
| Namespaces | Uses namespace context to avoid name collisions |
| Substitution groups and wildcards | Generates compatible model shapes where possible |

## Limitations

The XML Schema input type is for generating Python model definitions. It does not
implement XML parsing, XML serialization, or runtime XML validation.

Generated Pydantic v2 `pattern` constraints that combine distinct facets in the same
restriction, or inherited and derived facets, require **Pydantic 2.8 or later in
the environment that runs the generated code**, for every `--target-pydantic-version`
including `2`. These constraints use compiled
Python regular expressions to preserve full-value matching and inherited restrictions,
including when a generated type alias is used directly with `TypeAdapter`.
[Pydantic 2.8 added support for compiled patterns](https://pydantic.dev/articles/pydantic-v2-8-release#respect-regex-flags-in-constrained-string-validation).
This does not add generated validators or change the Pydantic requirement for other
XSD inputs or input formats. Upgrading only the environment that runs
datamodel-code-generator does not upgrade the generated code's runtime.

Python properties use XML local names. Generation fails when distinct XML names
would overwrite the same property, such as an element and attribute named `code`,
or elements named `code` in different namespaces. Repeated uses of the same XML
name remain supported.

## 📖 See Also

- 🖥️ [CLI Reference](cli-reference/index.md) - Complete CLI options reference
- 📊 [Supported Data Types](supported-data-types.md) - Data type support details
- 📋 [Generate from JSON Schema](jsonschema.md) - JSON Schema input documentation
