<!-- related-cli-options: --aliases, --no-alias -->

# 🏷️ Field Aliases

The `--aliases` option allows you to rename fields in the generated models. This is useful when you want to use different Python field names than those defined in the schema while preserving the original names as serialization aliases.

## 🚀 Basic Usage

```bash
datamodel-codegen \
    --input schema.json \
    --output-model-type pydantic_v2.BaseModel \
    --output model.py \
    --aliases aliases.json
```

You can also pass the same mapping inline:

```bash
datamodel-codegen \
    --input schema.json \
    --output-model-type pydantic_v2.BaseModel \
    --output model.py \
    --aliases '{"id": "id_", "type": "type_"}'
```

## 📋 Alias Format

The alias JSON maps original field names to their Python aliases. Pass it either inline or as a JSON file path.

### 📝 Flat Format (Traditional)

The simplest format applies aliases to all fields with the matching name, regardless of which class they belong to:

```json
{
  "id": "id_",
  "type": "type_",
  "class": "class_"
}
```

This will rename all fields named `id` to `id_`, all fields named `type` to `type_`, etc.

### 🎯 Scoped Format (Class-Specific)

When you have the same field name in multiple classes but want different aliases for each, use the scoped format with `ClassName.field`:

```json
{
  "User.name": "user_name",
  "Address.name": "address_name",
  "name": "default_name"
}
```

**⚡ Priority**: Scoped aliases take priority over flat aliases. In the example above:

- `User.name` will be renamed to `user_name`
- `Address.name` will be renamed to `address_name`
- Any other class with a `name` field will use `default_name`

## 📝 Example

### 📥 Input Schema

```json
{
  "type": "object",
  "title": "Root",
  "properties": {
    "name": {"type": "string"},
    "user": {
      "type": "object",
      "title": "User",
      "properties": {
        "name": {"type": "string"},
        "id": {"type": "integer"}
      }
    },
    "address": {
      "type": "object",
      "title": "Address",
      "properties": {
        "name": {"type": "string"},
        "city": {"type": "string"}
      }
    }
  }
}
```

### 🏷️ Alias Mapping

```json
{
  "Root.name": "root_name",
  "User.name": "user_name",
  "Address.name": "address_name"
}
```

### ✨ Generated Output

```python
from pydantic import BaseModel, Field


class User(BaseModel):
    user_name: str | None = Field(None, alias="name")
    id: int | None = None


class Address(BaseModel):
    address_name: str | None = Field(None, alias="name")
    city: str | None = None


class Root(BaseModel):
    root_name: str | None = Field(None, alias="name")
    user: User | None = None
    address: Address | None = None
```

## 📌 Notes

- The `ClassName` in scoped format must match the generated Python class name (after title conversion)
- For `allOf` models, the final generated class name includes any configured prefix, suffix, or collision suffix. A matching final-name alias takes priority over a legacy alias using the original schema name; legacy aliases remain a fallback when no final-name alias is supplied.
- When using `--use-title-as-name`, the class name is derived from the `title` property in the schema
- Aliases are applied during code generation, so the original field names are preserved as Pydantic `alias` values for proper serialization/deserialization

---

## 📖 See Also

- 🖥️ [CLI Reference: `--aliases`](cli-reference/field-customization.md#aliases) - Detailed CLI option documentation with examples
