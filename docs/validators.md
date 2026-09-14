<!-- related-cli-options: --validators -->

# Field Validators

The `--validators` option allows you to add custom field validators to generated Pydantic v2 models. This enables you to inject validation logic into generated code without manually editing it.

## Basic Usage

```bash
datamodel-codegen --input schema.json --output model.py \
  --validators validators.json \
  --use-annotated \
  --output-model-type pydantic_v2.BaseModel
```

You can also pass the same JSON object inline:

```bash
datamodel-codegen --input schema.json --output model.py \
  --validators '{"User":{"validators":[{"field":"name","function":"myapp.validators.validate_name"}]}}' \
  --use-annotated \
  --output-model-type pydantic_v2.BaseModel
```

## Validators Format

The validators JSON maps model names to their validator definitions. Pass it either inline or as a JSON file path.

### Structure

```json
{
  "ModelName": {
    "validators": [
      {
        "field": "field_name",
        "function": "module.path.to.validator_function",
        "mode": "after"
      }
    ]
  }
}
```

### Fields

| Field | Description | Required |
|-------|-------------|----------|
| `field` | Single field name to validate | One of `field` or `fields` |
| `fields` | List of field names (for multi-field validators) | One of `field` or `fields` |
| `function` | Fully qualified path to the validator function | Yes |
| `mode` | Validator mode: `before`, `after`, `wrap`, or `plain` | No (default: `after`) |

## Validator Modes

Pydantic v2 supports different validator modes, each with its own signature:

### `before` / `after` Mode

Standard validators that run before or after Pydantic's own validation:

```python
def validate_name(v: Any, info: ValidationInfo) -> Any:
    if not v:
        raise ValueError("Name cannot be empty")
    return v.strip()
```

### `wrap` Mode

Wrap validators receive a handler to call the next validator in the chain:

```python
from pydantic import ValidationInfo, ValidatorFunctionWrapHandler


def wrap_validate_name(v: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo) -> Any:
    # Pre-processing
    v = v.strip() if isinstance(v, str) else v
    # Call next validator
    result = handler(v)
    # Post-processing
    return result.upper()
```

### `plain` Mode

Plain validators replace Pydantic's validation entirely:

```python
def plain_validate_name(v: Any) -> str:
    if not isinstance(v, str):
        raise TypeError("Expected string")
    return v
```

## Example

### Input Schema

```json
{
  "type": "object",
  "title": "User",
  "properties": {
    "name": {"type": "string"},
    "email": {"type": "string", "format": "email"},
    "age": {"type": "integer", "minimum": 0}
  },
  "required": ["name", "email"]
}
```

### Validators File

```json
{
  "User": {
    "validators": [
      {
        "field": "name",
        "function": "myapp.validators.validate_name",
        "mode": "before"
      },
      {
        "field": "email",
        "function": "myapp.validators.validate_email",
        "mode": "after"
      },
      {
        "fields": ["name", "email"],
        "function": "myapp.validators.validate_contact_info",
        "mode": "after"
      }
    ]
  }
}
```

### Validator Functions (myapp/validators.py)

```python
from typing import Any
from pydantic import ValidationInfo


def validate_name(v: Any, info: ValidationInfo) -> Any:
    if isinstance(v, str):
        return v.strip()
    return v


def validate_email(v: Any, info: ValidationInfo) -> Any:
    if isinstance(v, str) and not v.endswith("@example.com"):
        # Custom email domain validation
        pass
    return v


def validate_contact_info(v: Any, info: ValidationInfo) -> Any:
    # This runs for both name and email fields
    return v
```

### Generated Output

```python
from __future__ import annotations

from typing import Annotated, Any

from myapp.validators import validate_contact_info, validate_email, validate_name
from pydantic import BaseModel, EmailStr, Field, ValidationInfo, field_validator


class User(BaseModel):
    name: str
    email: EmailStr
    age: Annotated[int | None, Field(ge=0)] = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name_validator(cls, v: Any, info: ValidationInfo) -> Any:
        return validate_name(v, info)

    @field_validator("email", mode="after")
    @classmethod
    def validate_email_validator(cls, v: Any, info: ValidationInfo) -> Any:
        return validate_email(v, info)

    @field_validator("name", "email", mode="after")
    @classmethod
    def validate_contact_info_validator(cls, v: Any, info: ValidationInfo) -> Any:
        return validate_contact_info(v, info)
```

## Notes

- This feature only supports Pydantic v2 (`--output-model-type pydantic_v2.BaseModel`)
- The `ModelName` in the validators JSON must match the generated Python class name
- Validator functions are imported automatically based on the `function` path
- When validator method names collide with another validator, an existing field, or an inherited validator, an incrementing suffix (`_1`, `_2`, etc.) is added in registration order.
- Functions with the same name from different modules receive stable import aliases using the same suffix format. Repeated uses of the same function share its import binding; every validator registration is retained.

---

## See Also

- [CLI Reference: `--validators`](cli-reference/template-customization.md#validators) - CLI option documentation
- [Pydantic v2 Validators Documentation](https://docs.pydantic.dev/latest/concepts/validators/) - Official Pydantic documentation
