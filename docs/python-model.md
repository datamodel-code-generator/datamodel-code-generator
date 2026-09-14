# 🐍 Generate from Python Models {#python-model}

Generate code from existing Python types: Pydantic models, dataclasses, TypedDict, or dict schemas. This is useful for converting between model types or generating from programmatically-defined schemas.

## 🚀 Quick Start {#quick-start}

```bash
datamodel-codegen --input-model ./mymodule.py:User --output model.py
```

## 🔄 Convert Between Python Model Styles {#convert-between-python-model-styles}

`--input-model` can retarget an existing Python model into another supported output model type. This is useful when
moving a codebase between model frameworks, or when one boundary needs validation models while another boundary needs
plain typed dictionaries.

```bash
# Pydantic BaseModel -> TypedDict
datamodel-codegen \
  --input-model ./models.py:User \
  --output-model-type typing.TypedDict \
  --output user_types.py

# TypedDict -> Pydantic v2
datamodel-codegen \
  --input-model ./types.py:User \
  --output-model-type pydantic_v2.BaseModel \
  --output user_model.py
```

## Format {#format}

```
--input-model <path/to/file.py>:<ObjectName>
```

Simply specify the **file path** and **object name** separated by `:`.

### Example {#format-example}

```
myproject/
├── src/
│   └── models/
│       └── user.py      # class User(BaseModel): ...
└── schemas.py           # USER_SCHEMA = {...}
```

```bash
# From file path (recommended - easy to copy-paste)
datamodel-codegen --input-model src/models/user.py:User --output model.py
datamodel-codegen --input-model ./schemas.py:USER_SCHEMA --input-file-type jsonschema

# Windows paths also work
datamodel-codegen --input-model src\models\user.py:User
```

!!! tip "Copy-paste friendly"
    Just copy the file path from your editor or file explorer, add `:ClassName`, and you're done!

### Module format (alternative) {#module-format}

You can also use Python module notation with dots:

```bash
datamodel-codegen --input-model src.models.user:User
datamodel-codegen --input-model schemas:USER_SCHEMA --input-file-type jsonschema
```

!!! note "Current directory is auto-added to `sys.path`"
    No `PYTHONPATH` configuration needed.

!!! tip "File and package name conflict"
    If both `mymodule.py` and `mymodule/` directory exist, use `./` prefix:
    ```bash
    datamodel-codegen --input-model ./mymodule.py:Model
    ```

---

## Supported Input Types {#supported-input-types}

| Type | Description | Requires |
|------|-------------|----------|
| Pydantic BaseModel | Pydantic v2 models with `model_json_schema()` | Pydantic v2 |
| dataclass | Standard library `@dataclass` | Pydantic v2 (for TypeAdapter) |
| Pydantic dataclass | `@pydantic.dataclasses.dataclass` | Pydantic v2 |
| TypedDict | `typing.TypedDict` subclasses | Pydantic v2 (for TypeAdapter) |
| dict | Dict containing JSON Schema or OpenAPI spec | - |

!!! note "Pydantic v2 Required"
    All Python type inputs (except raw dict) require Pydantic v2 runtime to convert to JSON Schema.

---

## 📝 Examples {#examples}

### Pydantic BaseModel {#pydantic-basemodel}

**mymodule.py**
```python
from pydantic import BaseModel


class User(BaseModel):
    name: str
    age: int
```

```bash
datamodel-codegen --input-model ./mymodule.py:User --output model.py
```

**✨ Generated model.py**
```python
from __future__ import annotations

from pydantic import BaseModel


class User(BaseModel):
    name: str
    age: int
```

### Convert Pydantic to TypedDict {#convert-pydantic-to-typeddict}

```bash
datamodel-codegen --input-model ./mymodule.py:User --output-model-type typing.TypedDict --output model.py
```

**✨ Generated model.py**
```python
from __future__ import annotations

from typing import TypedDict


class User(TypedDict):
    name: str
    age: int
```

### Standard dataclass {#dataclass}

**mymodule.py**
```python
from dataclasses import dataclass


@dataclass
class User:
    name: str
    age: int
```

```bash
datamodel-codegen --input-model ./mymodule.py:User --output model.py
```

**✨ Generated model.py**
```python
from __future__ import annotations

from pydantic import BaseModel


class User(BaseModel):
    name: str
    age: int
```

### TypedDict {#typeddict}

**mymodule.py**
```python
from typing import TypedDict


class User(TypedDict):
    name: str
    age: int
```

```bash
datamodel-codegen --input-model ./mymodule.py:User --output model.py
```

**✨ Generated model.py**
```python
from __future__ import annotations

from pydantic import BaseModel, Field


class User(BaseModel):
    name: str = Field(..., title="Name")
    age: int = Field(..., title="Age")
```

### Dict Schema (JSON Schema) {#dict-jsonschema}

**mymodule.py**
```python
USER_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
    },
    "required": ["name", "age"],
}
```

```bash
datamodel-codegen --input-model ./mymodule.py:USER_SCHEMA --input-file-type jsonschema --output model.py
```

**✨ Generated model.py**
```python
from __future__ import annotations

from pydantic import BaseModel


class Model(BaseModel):
    name: str
    age: int
```

!!! warning "Dict requires --input-file-type"
    When using a dict schema, you must specify `--input-file-type` (e.g., `jsonschema`, `openapi`).

### Dict Schema (OpenAPI) {#dict-openapi}

**mymodule.py**
```python
OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "API", "version": "1.0.0"},
    "paths": {},
    "components": {
        "schemas": {
            "User": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
            }
        }
    },
}
```

```bash
datamodel-codegen --input-model ./mymodule.py:OPENAPI_SPEC --input-file-type openapi --output model.py
```

**✨ Generated model.py**
```python
from __future__ import annotations

from pydantic import BaseModel


class User(BaseModel):
    name: str | None = None
    age: int | None = None
```

---

## Custom Python Types with x-python-type {#x-python-type}

When using `x-python-type` in JSON Schema (via `WithJsonSchema` in Pydantic), the generator automatically resolves and generates the required imports.

### Automatic Import Resolution {#import-resolution}

The generator supports many common Python types out of the box:

| Module | Supported Types |
|--------|-----------------|
| `typing` | `Any`, `Union`, `Optional`, `Literal`, `Final`, `ClassVar`, `Annotated`, `TypeVar`, `TypeAlias`, `Never`, `NoReturn`, `Self`, `LiteralString`, `TypeGuard`, `Type` |
| `collections` | `defaultdict`, `OrderedDict`, `Counter`, `deque`, `ChainMap` |
| `collections.abc` | `Callable`, `Iterable`, `Iterator`, `Generator`, `Awaitable`, `Coroutine`, `AsyncIterable`, `AsyncIterator`, `AsyncGenerator`, `Mapping`, `MutableMapping`, `Sequence`, `MutableSequence`, `Set`, `MutableSet`, `Collection`, `Reversible` |
| `pathlib` | `Path`, `PurePath` |
| `decimal` | `Decimal` |
| `uuid` | `UUID` |
| `datetime` | `datetime`, `date`, `time`, `timedelta` |
| `enum` | `Enum`, `IntEnum`, `StrEnum`, `Flag`, `IntFlag` |
| `re` | `Pattern`, `Match` |

For types not in this list, the generator dynamically searches common modules to resolve imports.

### Example {#x-python-type-example}

**mymodule.py**
```python
from collections import defaultdict
from typing import Any, Annotated
from pydantic import BaseModel, Field, WithJsonSchema


class Config(BaseModel):
    data: (
        Annotated[
            defaultdict[str, Annotated[dict[str, Any], Field(default_factory=dict)]],
            WithJsonSchema({"type": "object", "x-python-type": "defaultdict[str, dict[str, Any]]"}),
        ]
        | None
    ) = None
```

```bash
datamodel-codegen --input-model ./mymodule.py:Config --output-model-type typing.TypedDict
```

**✨ Generated output**
```python
from __future__ import annotations

from collections import defaultdict
from typing import Any, TypedDict

from typing_extensions import NotRequired


class Config(TypedDict):
    data: NotRequired[defaultdict[str, dict[str, Any]] | None]
```

!!! tip "Fully Qualified Paths"
    You can also use fully qualified paths in `x-python-type` (e.g., `collections.defaultdict`), which are always resolved correctly regardless of the static mapping.

---

## Mutual Exclusion {#mutual-exclusion}

`--input-model` cannot be used with:

- `--input` (file input)
- `--url` (URL input)
- `--watch` (file watching)

## Supported Python Model Features {#supported-python-model-features}

| Input type | Generation behavior |
|------------|---------------------|
| Pydantic v2 `BaseModel` | Uses `model_json_schema()` |
| Standard dataclasses | Uses Pydantic TypeAdapter to derive schema |
| Pydantic dataclasses | Uses Pydantic schema support |
| TypedDict | Uses Pydantic TypeAdapter to derive schema |
| Dict schemas | Treated as JSON Schema or OpenAPI based on `--input-file-type` |

## Limitations {#limitations}

Python model input imports and evaluates the specified object, so the target module must be importable and safe to
import in your environment. Raw dict input requires `--input-file-type` because the generator cannot infer whether the
dict is JSON Schema, OpenAPI, or another schema profile.

---

## 📖 See Also

- 🚀 [Getting Started](getting-started.md) - Installation and first model
- 🖥️ [CLI Reference](cli-reference/index.md) - Complete CLI options reference
- 📁 [CLI Reference: Base Options](cli-reference/base-options.md#input-model) - `--input-model` option details
- 📋 [Generate from JSON Schema](jsonschema.md) - JSON Schema input documentation
