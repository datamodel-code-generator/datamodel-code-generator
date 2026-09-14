<!-- related-cli-options: --target-python-version, --use-union-operator, --use-standard-collections, --use-annotated, --disable-future-imports, --use-generic-container-types -->

# Python Version Compatibility

datamodel-code-generator can generate code compatible with different Python versions. This page explains how to control type annotation syntax and imports for your target environment.

## Quick Overview

| Option | Description |
|--------|-------------|
| `--target-python-version` | Set the minimum Python version for generated code |
| `--use-union-operator` | Use `X \| Y` instead of `Union[X, Y]` |
| `--use-standard-collections` | Use `list`, `dict` instead of `List`, `Dict` |
| `--use-annotated` | Use `Annotated` for field metadata |
| `--use-generic-container-types` | Use `Sequence`, `Mapping` instead of `list`, `dict` |
| `--disable-future-imports` | Don't add `from __future__ import annotations` |

---

## `--target-python-version`

Sets the minimum Python version for the generated code. This automatically adjusts type annotation syntax.

| Version | Union Syntax | Collection Syntax | Notes |
|---------|--------------|-------------------|-------|
| 3.10 | `X \| Y` | `list[T]`, `dict[K, V]` | Union operator available |
| 3.11 | `X \| Y` | `list[T]`, `dict[K, V]` | `Self` type available |
| 3.12+ | `X \| Y` | `list[T]`, `dict[K, V]` | `type` statement available |

### Example: Python 3.10+

```bash
datamodel-codegen --input schema.json --output models.py \
  --target-python-version 3.10
```

```python
class User(BaseModel):
    id: int
    tags: list[str]
    metadata: str | int | None = None
```

---

## `--use-union-operator`

Uses the `|` operator for union types instead of `Union[X, Y]`.

### Without `--use-union-operator`

```python
from typing import Union, Optional


class Item(BaseModel):
    value: Union[str, int]
    label: Optional[str] = None  # Same as Union[str, None]
```

### With `--use-union-operator`

```bash
datamodel-codegen --input schema.json --output models.py --use-union-operator
```

```python
class Item(BaseModel):
    value: str | int
    label: str | None = None
```

### Compatibility Note

The union operator `|` requires:
- Python 3.10+ at runtime, OR
- `from __future__ import annotations` (Python 3.7+) for postponed evaluation

---

## `--use-standard-collections`

Uses built-in collection types instead of `typing` module generics.

### Without `--use-standard-collections`

```python
from typing import List, Dict, Set, Tuple


class Data(BaseModel):
    items: List[str]
    mapping: Dict[str, int]
    unique: Set[str]
    pair: Tuple[str, int]
```

### With `--use-standard-collections`

```bash
datamodel-codegen --input schema.json --output models.py --use-standard-collections
```

```python
class Data(BaseModel):
    items: list[str]
    mapping: dict[str, int]
    unique: set[str]
    pair: tuple[str, int]
```

### Compatibility Note

Built-in generic syntax requires:
- Python 3.10+ at runtime, OR
- `from __future__ import annotations`

---

## `--use-annotated`

Uses `typing.Annotated` to attach metadata to types, which is the modern approach for Pydantic v2.

### Without `--use-annotated`

```python
from pydantic import Field


class User(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    age: int = Field(..., ge=0, le=150)
```

### With `--use-annotated`

```bash
datamodel-codegen --input schema.json --output models.py --use-annotated
```

```python
from typing import Annotated
from pydantic import Field


class User(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    age: Annotated[int, Field(ge=0, le=150)]
```

### Benefits

- Cleaner separation of type and constraints
- Better IDE support
- More compatible with other tools that understand `Annotated`
- Required types are more explicit

---

## `--use-generic-container-types`

Uses abstract container types (`Sequence`, `Mapping`, `FrozenSet`) instead of concrete types (`list`, `dict`, `set`).

```bash
datamodel-codegen --input schema.json --output models.py --use-generic-container-types
```

```python
from typing import Mapping, Sequence


class Data(BaseModel):
    items: Sequence[str]
    mapping: Mapping[str, int]
```

If `--use-standard-collections` is also set, imports from `collections.abc` instead of `typing`.

This is useful when:
- You want to use abstract types for better type flexibility
- Maintaining compatibility with interfaces that accept any sequence/mapping

---

## `--disable-future-imports`

Prevents adding `from __future__ import annotations` to generated files.

### Default behavior (with future imports)

```python
from __future__ import annotations


class User(BaseModel):
    friends: list[User]  # Forward reference works due to PEP 563
```

### With `--disable-future-imports`

```bash
datamodel-codegen --input schema.json --output models.py --disable-future-imports
```

```python
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import ForwardRef


class User(BaseModel):
    friends: List["User"]  # String forward reference
```

### When to disable

- Compatibility with runtime annotation inspection
- Libraries that don't support `__future__.annotations`
- When using `get_type_hints()` at runtime

---

## Common Patterns

### Pattern 1: Modern Python (3.10+)

For projects targeting Python 3.10 or later:

```bash
datamodel-codegen --input schema.json --output models.py \
  --target-python-version 3.10 \
  --use-union-operator \
  --use-standard-collections \
  --use-annotated
```

Output:
```python
from typing import Annotated
from pydantic import BaseModel, Field


class User(BaseModel):
    id: int
    name: Annotated[str, Field(min_length=1)]
    tags: list[str]
    metadata: dict[str, str] | None = None
```

### Pattern 2: Minimum Supported Python (3.10)

For projects targeting Python 3.10 (minimum supported version):

```bash
datamodel-codegen --input schema.json --output models.py \
  --target-python-version 3.10
```

Output:
```python
class User(BaseModel):
    id: int
    name: str
    tags: list[str]
    metadata: dict[str, str] | None = None
```

### Pattern 3: Maximum compatibility

For libraries that need to work across multiple Python versions (3.10+):

```bash
datamodel-codegen --input schema.json --output models.py \
  --target-python-version 3.10
```

The generator will use modern Python 3.10+ syntax including union operators and built-in generic types.

### Pattern 4: CI/CD consistency

Pin the Python version in `pyproject.toml` to ensure consistent output:

```toml
[tool.datamodel-codegen]
target-python-version = "3.10"
use-union-operator = true
use-standard-collections = true
use-annotated = true
```

---

## Version Feature Matrix

| Feature | 3.10 | 3.11 | 3.12+ |
|---------|------|------|-------|
| `list[T]` syntax | native | native | native |
| `X \| Y` union | native | native | native |
| `Annotated` | native | native | native |
| `TypeAlias` | native | native | native |
| `Self` | `typing_extensions` | native | native |
| `type` statement | N/A | N/A | native |

---

## Troubleshooting

### TypeError: 'type' object is not subscriptable

This occurs when using `list[T]` syntax on older Python versions without `__future__` imports.

**Solution:** Ensure you are running Python 3.10+ or use `from __future__ import annotations`.

### Pydantic validation fails with forward references

This can happen when `__future__.annotations` interacts poorly with Pydantic's type resolution.

**Solution:** Try `--disable-future-imports` or update to Pydantic v2.

### Python 3.13 DeprecationWarning with `typing._eval_type`

When running on Python 3.13+ with `from __future__ import annotations`, you may see:

```text
DeprecationWarning: Failing to pass a value to the 'type_params' parameter
of 'typing._eval_type' is deprecated...
```

This occurs because Python 3.13 deprecated calling `typing._eval_type()` without the `type_params` parameter. Libraries that evaluate forward references (like older Pydantic versions) trigger this warning.

**Solutions:**

1. **Upgrade Pydantic** (recommended):
   - Pydantic v2: Upgrade to the latest version

2. **Use `--disable-future-imports`** as a workaround:
   ```bash
   datamodel-codegen --input schema.json --output models.py --disable-future-imports
   ```

3. **Suppress the warning** in pytest (temporary fix):
   ```toml
   # pyproject.toml
   [tool.pytest.ini_options]
   filterwarnings = [
       "ignore::DeprecationWarning:pydantic.typing",
   ]
   ```

!!! note
    Python 3.14+ will use native deferred annotations (PEP 649), and the generator
    will no longer add `from __future__ import annotations` for those versions.

### IDE shows type errors

Some IDEs don't fully understand `from __future__ import annotations`.

**Solution:** Configure your IDE's Python version or use explicit type syntax matching your runtime version.

---

## See Also

- [CLI Reference: `--target-python-version`](cli-reference/model-customization.md#target-python-version)
- [CLI Reference: `--use-union-operator`](cli-reference/typing-customization.md#use-union-operator)
- [CLI Reference: `--use-standard-collections`](cli-reference/typing-customization.md#use-standard-collections)
- [CLI Reference: `--use-annotated`](cli-reference/typing-customization.md#use-annotated)
- [Output Model Types](output-model-types.md)
- [Type Mappings and Custom Types](type-mappings.md)
- [CI/CD Integration](ci-cd.md)
