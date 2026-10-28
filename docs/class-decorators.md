<!-- related-cli-options: --class-decorators, --additional-imports -->

# Custom Class Decorators

The `--class-decorators` option adds custom decorators to all generated model classes. This is useful for integrating with serialization libraries like `dataclasses_json`, or adding custom behavior to your models.

## Why use this?

When using `dataclasses.dataclass` output with `--snake-case-field`, Python field names are snake_case but the original JSON keys may be camelCase. Libraries like `dataclasses_json` can handle this conversion automatically via decorators.

## Example: Using dataclasses_json

Convert a JSON Schema with camelCase properties to dataclasses with snake_case fields that serialize back to camelCase.

**schema.json**
```json
{
  "type": "object",
  "title": "User",
  "properties": {
    "firstName": { "type": "string" },
    "lastName": { "type": "string" },
    "emailAddress": { "type": "string" }
  },
  "required": ["firstName", "lastName"]
}
```

### Without `--class-decorators`

```bash
datamodel-codegen --input schema.json \
  --output-model-type dataclasses.dataclass \
  --snake-case-field
```

**Generated model.py**
```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class User:
    first_name: str
    last_name: str
    email_address: str | None = None
```

The field names are snake_case, but there's no way to map them back to the original camelCase JSON keys.

---

### With `--class-decorators`

```bash
datamodel-codegen --input schema.json \
  --output-model-type dataclasses.dataclass \
  --snake-case-field \
  --class-decorators "@dataclass_json(letter_case=LetterCase.CAMEL)" \
  --additional-imports "dataclasses_json.dataclass_json,dataclasses_json.LetterCase"
```

**Generated model.py**
```python
from __future__ import annotations

from dataclasses import dataclass

from dataclasses_json import LetterCase, dataclass_json


@dataclass_json(letter_case=LetterCase.CAMEL)
@dataclass
class User:
    first_name: str
    last_name: str
    email_address: str | None = None
```

Now serialization automatically converts between snake_case and camelCase:

```python
user = User(first_name="John", last_name="Doe")
print(user.to_json())
# {"firstName": "John", "lastName": "Doe", "emailAddress": null}
```

## Usage Notes

- **Multiple decorators**: Use comma separation for multiple decorators:
  ```bash
  --class-decorators "@decorator1,@decorator2"
  ```

- **@ prefix is optional**: Both `@dataclass_json` and `dataclass_json` work - the `@` is added automatically if missing.

- **Combine with `--additional-imports`**: Always add the required imports for your decorators using `--additional-imports`.

## Other Use Cases

The `--class-decorators` option works with any output model type:

- **Pydantic models**: Add custom validators or behavior
- **TypedDict**: Add runtime type-checking decorators
- **msgspec.Struct**: Add custom serialization hooks

## See Also

- [CLI Reference: `--class-decorators`](cli-reference/template-customization.md#class-decorators) - Detailed CLI option documentation
- [CLI Reference: `--additional-imports`](cli-reference/template-customization.md#additional-imports) - Adding custom imports

## Related Issues

- [#2358](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/2358) - Feature request for dataclasses_json support
