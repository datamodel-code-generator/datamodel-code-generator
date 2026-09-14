<!-- related-cli-options: --reuse-model, --reuse-scope, --shared-module-name, --collapse-root-models, --disable-warnings, --use-type-alias -->

# Model Reuse and Deduplication

When generating models from schemas, you may encounter duplicate model definitions. datamodel-code-generator provides options to deduplicate models and share them across multiple files, improving output structure, reducing diff sizes, and enhancing performance.

## Quick Overview

| Option | Description |
|--------|-------------|
| `--reuse-model` | Deduplicate identical model/enum definitions |
| `--reuse-scope` | Control scope of deduplication (`root` or `tree`) |
| `--shared-module-name` | Name for shared module in multi-file output |
| `--collapse-root-models` | Inline root models instead of creating wrappers |
| `--use-type-alias` | Create TypeAlias for reusable field types (see [Reducing Duplicate Field Types](#reducing-duplicate-field-types)) |

---

## `--reuse-model`

The `--reuse-model` flag detects identical enum or model definitions and generates a single shared definition instead of duplicates.

### Without `--reuse-model`

```bash
datamodel-codegen --input schema.json --output model.py
```

```python
# Duplicate enums for animal and pet fields
class Animal(Enum):
    dog = "dog"
    cat = "cat"


class Pet(Enum):  # Duplicate!
    dog = "dog"
    cat = "cat"


class User(BaseModel):
    animal: Optional[Animal] = None
    pet: Optional[Pet] = None
```

### With `--reuse-model`

```bash
datamodel-codegen --input schema.json --output model.py --reuse-model
```

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: model-reuse.reuse-model.output -->
```python
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, RootModel


class Animal(Enum):
    dog = "dog"
    cat = "cat"
    snake = "snake"


class RedistributeEnum(Enum):
    static = "static"
    connected = "connected"


class User(BaseModel):
    name: str | None = None
    animal: Animal | None = "dog"
    pet: Animal | None = "cat"
    redistribute: list[RedistributeEnum] | None = None


class Redistribute(RootModel[list[RedistributeEnum]]):
    root: list[RedistributeEnum] = Field(..., description="Redistribute type for routes.")
```
<!-- END AUTO-GENERATED DOC EXAMPLE: model-reuse.reuse-model.output -->

### Benefits

- **Smaller output** - Less generated code
- **Cleaner diffs** - Changes to shared types only appear once
- **Better performance** - Faster generation for large schemas
- **Type consistency** - Same types are truly the same

---

## `--reuse-scope`

Controls the scope for model reuse detection when processing multiple input files.

| Value | Description |
|-------|-------------|
| `root` | Detect duplicates only within each input file (default) |
| `tree` | Detect duplicates across all input files |

### Single-file input

For single-file input, `--reuse-scope` has no effect. Use `--reuse-model` alone.

### Multi-file input with `tree` scope

When generating from multiple schema files to a directory:

```bash
datamodel-codegen --input schemas/ --output models/ --reuse-model --reuse-scope tree
```

**Input files:**
```text
schemas/
├── user.json      # defines SharedModel
└── order.json     # also defines identical SharedModel
```

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: model-reuse.reuse-scope-tree.output -->
**Output with `--reuse-scope tree`:**

```text
models/
|-- __init__.py
|-- schema_a.py
|-- schema_b.py
`-- shared.py
```

**models/__init__.py:**
_No code beyond the generated header._

**models/schema_a.py:**

```python
from __future__ import annotations

from pydantic import BaseModel

from .shared import SharedModel as SharedModel_1


class SharedModel(SharedModel_1):
    pass


class Model(BaseModel):
    data: SharedModel | None = None
```

**models/schema_b.py:**

```python
from __future__ import annotations

from pydantic import BaseModel

from . import shared


class Model(BaseModel):
    info: shared.SharedModel | None = None
```

**models/shared.py:**

```python
from __future__ import annotations

from pydantic import BaseModel


class SharedModel(BaseModel):
    id: int | None = None
    name: str | None = None
```
<!-- END AUTO-GENERATED DOC EXAMPLE: model-reuse.reuse-scope-tree.output -->

---

## `--shared-module-name`

Customize the name of the shared module when using `--reuse-scope tree`.

```bash
datamodel-codegen --input schemas/ --output models/ \
  --reuse-model --reuse-scope tree --shared-module-name common
```

**Output:**
```text
models/
├── __init__.py
├── user.py
├── order.py
└── common.py      # Instead of shared.py
```

---

## `--collapse-root-models`

Inline root model definitions instead of creating separate wrapper classes.

### Without `--collapse-root-models`

```python
class UserId(BaseModel):
    __root__: str


class User(BaseModel):
    id: UserId
```

### With `--collapse-root-models`

```python
class User(BaseModel):
    id: str  # Inlined
```

### When to use

- Simpler output when wrapper classes aren't needed
- Reducing the number of generated classes
- When root models are just type aliases

---

## Combining Options

### Recommended for large multi-file projects

```bash
datamodel-codegen \
  --input schemas/ \
  --output models/ \
  --reuse-model \
  --reuse-scope tree \
  --shared-module-name common \
  --collapse-root-models
```

This produces:
- Deduplicated models across all files
- Shared types in a `common.py` module
- Inlined simple root models
- Minimal, clean output

### Recommended for single-file projects

```bash
datamodel-codegen \
  --input schema.json \
  --output model.py \
  --reuse-model \
  --collapse-root-models
```

---

## Performance Impact

For large schemas with many models:

| Scenario | Without reuse | With reuse |
|----------|---------------|------------|
| 100 schemas, 50% duplicates | 100 models | ~50 models |
| Generation time | Baseline | Faster (less to generate) |
| Output size | Large | Smaller |
| Git diff on type change | Multiple files | Single location |

!!! tip "Performance tip"
    For very large schemas, combine `--reuse-model` with `--disable-warnings` to speed up generation:

    ```bash
    datamodel-codegen --reuse-model --disable-warnings --input large-schema.json
    ```

---

## Output Structure Comparison

### Without deduplication

```text
models/
├── user.py         # UserStatus enum
├── order.py        # OrderStatus enum (duplicate of UserStatus!)
└── product.py      # ProductStatus enum (duplicate!)
```

### With `--reuse-model --reuse-scope tree`

```text
models/
├── __init__.py
├── user.py         # imports Status from shared
├── order.py        # imports Status from shared
├── product.py      # imports Status from shared
└── shared.py       # Status enum defined once
```

---

## Reducing Duplicate Field Types

When multiple classes share the same field type with identical constraints or metadata, you can reduce duplication by defining the type once in `$defs` and referencing it with `$ref`. Combined with `--use-type-alias`, this creates a single TypeAlias that's reused across all classes.

### Problem: Duplicate Annotated Fields

Without using `$ref`, each class gets its own inline field definition:

```python
class ClassA(BaseModel):
    place_name: Annotated[str, Field(alias="placeName")]  # Duplicate!


class ClassB(BaseModel):
    place_name: Annotated[str, Field(alias="placeName")]  # Duplicate!
```

### Solution: Use `$defs` with `--use-type-alias`

**Step 1: Define the shared type in `$defs`**

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: model-reuse.use-type-alias.schema -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Model",
  "$defs": {
    "PlaceName": {
      "type": "string",
      "title": "PlaceName",
      "description": "A place name with alias",
      "examples": ["Tokyo", "New York"]
    },
    "ClassA": {
      "type": "object",
      "title": "ClassA",
      "properties": {
        "place_name": {
          "$ref": "#/$defs/PlaceName"
        },
        "other_field": {
          "type": "integer"
        }
      },
      "required": ["place_name"]
    },
    "ClassB": {
      "type": "object",
      "title": "ClassB",
      "properties": {
        "place_name": {
          "$ref": "#/$defs/PlaceName"
        },
        "description": {
          "type": "string"
        }
      },
      "required": ["place_name"]
    }
  },
  "anyOf": [
    {"$ref": "#/$defs/ClassA"},
    {"$ref": "#/$defs/ClassB"}
  ]
}
```
<!-- END AUTO-GENERATED DOC EXAMPLE: model-reuse.use-type-alias.schema -->

**Step 2: Generate with `--use-type-alias`**

```bash
datamodel-codegen \
  --input schema.json \
  --output model.py \
  --use-type-alias
```

### Result: Single TypeAlias reused across classes

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: model-reuse.use-type-alias.output -->
```python
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field
from typing_extensions import TypeAliasType

PlaceName = TypeAliasType(
    "PlaceName",
    Annotated[
        str,
        Field(
            ...,
            description="A place name with alias",
            examples=["Tokyo", "New York"],
            title="PlaceName",
        ),
    ],
)


class ClassA(BaseModel):
    place_name: PlaceName
    other_field: int | None = None


class ClassB(BaseModel):
    place_name: PlaceName
    description: str | None = None


Model = TypeAliasType("Model", Annotated[ClassA | ClassB, Field(..., title="Model")])
```
<!-- END AUTO-GENERATED DOC EXAMPLE: model-reuse.use-type-alias.output -->

### Benefits

- **Single source of truth** - Field type is defined once
- **Easier maintenance** - Change the type in one place
- **Cleaner generated code** - No redundant annotations
- **Type safety** - All fields share the exact same type

### When to Use This Pattern

This pattern is ideal when:

- Multiple classes share fields with the same constraints (e.g., `minLength`, `pattern`)
- Fields have identical metadata (e.g., `description`, `examples`)
- You want to ensure type consistency across your schema

---

## See Also

- [CLI Reference: `--reuse-model`](cli-reference/model-customization.md#reuse-model)
- [CLI Reference: `--reuse-scope`](cli-reference/model-customization.md#reuse-scope)
- [CLI Reference: `--collapse-root-models`](cli-reference/model-customization.md#collapse-root-models)
- [CLI Reference: `--use-type-alias`](cli-reference/typing-customization.md#use-type-alias)
- [Root Models and Type Aliases](root-model-and-type-alias.md)
- [FAQ: Performance](faq.md#-performance)
