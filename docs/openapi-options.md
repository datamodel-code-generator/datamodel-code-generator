<!-- related-cli-options: --openapi-scopes, --include-path-parameters, --use-operation-id-as-name, --read-only-write-only-model-type, --validation -->

# OpenAPI Options

When working with OpenAPI specifications, datamodel-code-generator provides several options to control how schemas, operations, and special properties are handled. This page explains when and how to use each option.

This page is a task-oriented guide for OpenAPI-specific generation behavior. For input format basics, see
[Generate from OpenAPI](openapi.md). For every flag, choice, and generated example, see
[CLI Reference: OpenAPI-only Options](cli-reference/openapi-only-options.md).

## Quick Overview

| Option | Description |
|--------|-------------|
| `--openapi-scopes` | Select which parts of the spec to generate models from |
| `--include-path-parameters` | Include path parameters in generated models |
| `--use-operation-id-as-name` | Name models using operation IDs |
| `--read-only-write-only-model-type` | Generate separate models for request/response contexts |
| `--validation` | Enable OpenAPI validation constraints (deprecated) |

---

## `--openapi-scopes`

Controls which sections of the OpenAPI specification to generate models from.

| Scope | Description |
|-------|-------------|
| `schemas` | Generate from `#/components/schemas` (default) |
| `parameters` | Include parameter models for operations selected by `paths` or `webhooks` |
| `paths` | Generate models from path operation request bodies and responses |
| `webhooks` | Generate models from webhook operation request bodies and responses |

### Default behavior (schemas only)

```bash
datamodel-codegen --input openapi.yaml --output models.py
```

Generates models only from `#/components/schemas`.

### Include operation schemas

```bash
datamodel-codegen --input openapi.yaml --output models.py \
  --openapi-scopes schemas paths
```

Also generates models from operation request bodies and responses.

### Include operation parameter models

```bash
datamodel-codegen --input openapi.yaml --output models.py \
  --openapi-scopes schemas parameters paths
```

Also generates a query parameter model for each operation that has query parameters.
Add `--include-path-parameters` to include URL path parameters in these models.
Parameters can be declared inline or referenced from `#/components/parameters`.
Unreferenced entries in `components.parameters` do not generate standalone models,
and the `parameters` scope alone does not select operations.

### When to use each scope

| Use Case | Recommended Scopes |
|----------|-------------------|
| Basic model generation | `schemas` (default) |
| Request and response models | `schemas paths` |
| Request, response, and query parameter models | `schemas paths parameters` |

---

## `--include-path-parameters`

Includes path parameters as fields in generated operation parameter models.
Use this with `--openapi-scopes paths parameters`.

### OpenAPI Example

```yaml
openapi: "3.0.3"
info:
  title: Orders API
  version: "1.0"
paths:
  /users/{user_id}/orders/{order_id}:
    get:
      operationId: getOrder
      parameters:
        - name: user_id
          in: path
          required: true
          schema:
            type: string
        - name: order_id
          in: path
          required: true
          schema:
            type: integer
      responses:
        '204':
          description: No content
```

### Without `--include-path-parameters`

Only query parameters are included in operation parameter models. This example
has only path parameters, so it does not generate a parameter model without the flag.

### With `--include-path-parameters`

```bash
datamodel-codegen --input openapi.yaml --output models.py \
  --openapi-scopes paths parameters --include-path-parameters --use-operation-id-as-name
```

```python
class GetOrderParameters(BaseModel):
    user_id: str
    order_id: int
```

### When to use

- Building request validation models that include URL parameters
- Grouping query and URL path parameters in one operation parameter model

---

## `--use-operation-id-as-name`

Uses the `operationId` from OpenAPI operations to name generated models instead of deriving names from paths.

### OpenAPI Example

```yaml
paths:
  /users/{id}:
    get:
      operationId: getUserById
      responses:
        '200':
          content:
            application/json:
              schema:
                type: object
                properties:
                  id: { type: integer }
                  name: { type: string }
```

### Without `--use-operation-id-as-name`

```python
class UsersIdGetResponse(BaseModel):  # Derived from path
    id: int
    name: str
```

### With `--use-operation-id-as-name`

```bash
datamodel-codegen --input openapi.yaml --output models.py --use-operation-id-as-name
```

```python
class GetUserByIdResponse(BaseModel):  # Uses operationId
    id: int
    name: str
```

### When to use

- When `operationId` values are well-designed and descriptive
- For consistency with generated API clients (e.g., OpenAPI Generator)
- When path-derived names are too verbose or unclear

---

## `--read-only-write-only-model-type`

Generates separate request/response model variants for properties marked as `readOnly` or `writeOnly` in OpenAPI.

See [CLI Reference: `--read-only-write-only-model-type`](cli-reference/openapi-only-options.md#read-only-write-only-model-type) for the full option reference.

### OpenAPI Example

```yaml
components:
  schemas:
    User:
      type: object
      properties:
        id:
          type: integer
          readOnly: true        # Only in responses
        password:
          type: string
          writeOnly: true       # Only in requests
        name:
          type: string          # In both
```

### Without `--read-only-write-only-model-type`

```python
class User(BaseModel):
    id: Optional[int] = None  # Both included
    password: Optional[str] = None
    name: Optional[str] = None
```

### With `--read-only-write-only-model-type request-response`

```bash
datamodel-codegen --input openapi.yaml --output models.py \
  --read-only-write-only-model-type request-response
```

```python
class UserRequest(BaseModel):
    """For requests - excludes readOnly fields."""

    password: Optional[str] = None
    name: Optional[str] = None


class UserResponse(BaseModel):
    """For responses - excludes writeOnly fields."""

    id: Optional[int] = None
    name: Optional[str] = None
```

Use `all` instead of `request-response` when you also need the base model with all fields.

### Values

| Value | Description |
|-------|-------------|
| `request-response` | Generate request and response variants |
| `all` | Generate the base model plus request and response variants |

### When to use

- APIs with distinct request/response schemas
- Strict type checking for API clients
- When `readOnly`/`writeOnly` properties are heavily used

---

## `--validation` (Deprecated)

!!! warning "Deprecated"
    Use `--field-constraints` instead. The `--validation` option is maintained for backward compatibility.

Enables validation constraints from OpenAPI schemas.

```bash
# Deprecated
datamodel-codegen --input openapi.yaml --output models.py --validation

# Recommended
datamodel-codegen --input openapi.yaml --output models.py --field-constraints
```

See [Field Constraints](field-constraints.md) for details.

---

## Common Patterns

### Pattern 1: Basic API models

For simple APIs where you only need schema models:

```bash
datamodel-codegen --input openapi.yaml --output models.py
```

### Pattern 2: Full API client models

For generating complete models for an API client:

```bash
datamodel-codegen --input openapi.yaml --output models/ \
  --openapi-scopes schemas parameters paths \
  --use-operation-id-as-name \
  --include-path-parameters
```

### Pattern 3: Strict request/response separation

For APIs with distinct input/output shapes:

```bash
datamodel-codegen --input openapi.yaml --output models/ \
  --read-only-write-only-model-type request-response \
  --field-constraints
```

### Pattern 4: Versioned API structure

For large APIs with versioned endpoints:

```bash
datamodel-codegen --input openapi.yaml --output models/ \
  --treat-dot-as-module \
  --use-operation-id-as-name \
  --all-exports-scope recursive
```

---

## OpenAPI Version Support

| OpenAPI Version | Support |
|-----------------|---------|
| 3.0.x | Full support |
| 3.1.x | Full support |
| 3.2.x | Full support |
| 2.0 (Swagger) | Partial support |

---

## See Also

- [CLI Reference: OpenAPI-only Options](cli-reference/openapi-only-options.md)
- [Field Constraints](field-constraints.md)
- [Module Structure and Exports](module-exports.md)
- [OpenAPI Input Format](openapi.md)
