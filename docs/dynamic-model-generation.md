# Dynamic Model Generation

Generate real Python model classes from JSON Schema or OpenAPI at runtime without writing files.

## Overview

While `generate()` produces source code as strings, `generate_dynamic_models()` creates actual Python classes that you can use immediately for validation and data processing. This is useful for:

- Runtime schema validation without code generation step
- Dynamic API clients that adapt to schema changes
- Testing and prototyping
- Plugin systems with dynamic schemas

## Quick Start

```python
from datamodel_code_generator import GenerateConfig, generate_dynamic_models

schema = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    "required": ["name"],
}

models = generate_dynamic_models(
    schema,
    config=GenerateConfig(class_name="User"),
    target_model_names=["User"],
)
User = models["User"]

# Use the model for validation
user = User(name="Alice", age=30)
print(user.model_dump())  # {'name': 'Alice', 'age': 30}

# Validation errors are raised
try:
    User(age="not a number")  # Missing required 'name', wrong type for 'age'
except Exception as e:
    print(e)
```

## API Reference

### `generate_dynamic_models()`

```python
def generate_dynamic_models(
    input_: Mapping[str, Any],
    *,
    config: GenerateConfig | None = None,
    cache_size: int = 128,
    module_name: str | None = None,
    target_model_names: Sequence[str] | None = None,
) -> dict[str, type]:
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `input_` | `Mapping[str, Any]` | required | JSON Schema or OpenAPI schema as dict |
| `config` | `GenerateConfig \| None` | `None` | Generation options (same as `generate()`) |
| `cache_size` | `int` | `128` | Maximum cached schemas. Set to `0` to disable |
| `module_name` | `str \| None` | `None` | Optional module/package name to assign to generated classes |
| `target_model_names` | `Sequence[str] \| None` | `None` | Optional model names to include in the returned dictionary. Generation still produces all referenced models internally |

**Returns:** `dict[str, type]` - Dictionary mapping class names to model classes.

### `generate_dynamic_module()`

```python
def generate_dynamic_module(
    input_: Mapping[str, Any],
    *,
    config: GenerateConfig | None = None,
    cache_size: int = 128,
    module_name: str | None = None,
) -> DynamicModule:
```

Generates the classes like `generate_dynamic_models()` and also tells which class the document itself became and
which class each schema location became. The parameters are those of `generate_dynamic_models()` without
`target_model_names`. See [Root Class and Schema Locations](#root-class-and-schema-locations).

**Returns:** `DynamicModule` - a frozen object with these attributes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `models` | `dict[str, type]` | Class names mapped to the classes, like the dictionary `generate_dynamic_models()` returns |
| `root_class` | `type \| None` | The class generated for the document itself (location `#`), or `None` |
| `models_by_source_ref` | `Mapping[str, type]` | A read-only map from a schema location to its class |
| `module_name` | `str \| None` | The module (the package for split output) the classes were executed in; `None` for an unnamed single module |

### `clear_dynamic_models_cache()`

```python
def clear_dynamic_models_cache() -> int:
```

Clears the internal cache and returns the number of entries cleared.

## Examples

### JSON Schema with Nested Models

```python
from datamodel_code_generator import generate_dynamic_models

schema = {
    "$defs": {
        "Address": {
            "type": "object",
            "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
            "required": ["street", "city"],
        },
        "Person": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "address": {"$ref": "#/$defs/Address"}},
            "required": ["name"],
        },
    },
    "$ref": "#/$defs/Person",
}

models = generate_dynamic_models(schema)

# Both models are available
Person = models["Person"]
Address = models["Address"]

person = Person(name="Bob", address={"street": "123 Main St", "city": "NYC"})
print(person.model_dump())
# {'name': 'Bob', 'address': {'street': '123 Main St', 'city': 'NYC'}}
```

### OpenAPI Schema

OpenAPI schemas are auto-detected:

```python
from datamodel_code_generator import generate_dynamic_models

openapi_schema = {
    "openapi": "3.0.0",
    "info": {"title": "User API", "version": "1.0.0"},
    "paths": {},
    "components": {
        "schemas": {
            "User": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "email": {"type": "string", "format": "email"}},
                "required": ["id", "email"],
            }
        }
    },
}

models = generate_dynamic_models(openapi_schema)
User = models["User"]

user = User(id=1, email="alice@example.com")
```

### With Custom Configuration

```python
from datamodel_code_generator import generate_dynamic_models, GenerateConfig, DataModelType

schema = {"type": "object", "properties": {"name": {"type": "string"}}}

config = GenerateConfig(
    class_name="Customer",
    output_model_type=DataModelType.PydanticV2BaseModel,
)

models = generate_dynamic_models(schema, config=config, target_model_names=["Customer"])
Customer = models["Customer"]
```

### With a Runtime Module Name

```python
from datamodel_code_generator import GenerateConfig, generate_dynamic_models

schema = {"type": "object", "properties": {"name": {"type": "string"}}}

models = generate_dynamic_models(
    schema,
    config=GenerateConfig(class_name="Customer"),
    module_name="runtime_models",
    target_model_names=["Customer"],
)
Customer = models["Customer"]
assert Customer.__module__ == "runtime_models"
```

While the generated code runs, a module of that name is registered in `sys.modules`, unless one already exists, and
removed afterwards, so Pydantic can parameterize generic models such as `RootModel[list[int]]` in it. A module of that
name that can be imported, such as the module of a `base_class` the generated code imports, is imported first and kept
instead.

### Filtering Returned Models

`target_model_names` filters the returned dictionary without pruning generation. Referenced models are still generated
so validation and forward references continue to work.

```python
from datamodel_code_generator import generate_dynamic_models

models = generate_dynamic_models(schema, target_model_names=["User", "Order"])

User = models["User"]
Order = models["Order"]
```

### Enum Models

```python
from datamodel_code_generator import generate_dynamic_models

schema = {"type": "object", "properties": {"status": {"type": "string", "enum": ["pending", "approved", "rejected"]}}}

models = generate_dynamic_models(schema)
Model = models["Model"]
Status = models["Status"]

# Enum validation
item = Model(status="approved")
print(item.status)  # Status.approved
print(item.status.value)  # 'approved'

# Invalid enum value raises error
try:
    Model(status="invalid")
except Exception as e:
    print(e)
```

### Circular References

```python
from datamodel_code_generator import generate_dynamic_models

schema = {
    "$defs": {
        "Node": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
            },
        }
    },
    "$ref": "#/$defs/Node",
}

models = generate_dynamic_models(schema)
Node = models["Node"]

tree = Node(
    value="root",
    children=[
        Node(value="child1", children=[]),
        Node(value="child2", children=[Node(value="grandchild", children=[])]),
    ],
)
```

## Root Class and Schema Locations

`generate_dynamic_module()` finds the class of the document itself and the class of each schema location, so you
need not guess them from titles or class names:

```python
from datamodel_code_generator import generate_dynamic_module

schema = {
    "title": "Root",
    "type": "object",
    "properties": {
        "child": {"$ref": "#/$defs/Root"},
        "address": {"type": "object", "properties": {"street": {"type": "string"}}},
    },
    "$defs": {"Root": {"type": "object", "properties": {"name": {"type": "string"}}}},
}

module = generate_dynamic_module(schema)
assert module.root_class is module.models["RootModel"]
assert module.models_by_source_ref["#/$defs/Root"] is module.models["Root"]
assert module.models_by_source_ref["#/properties/address"] is module.models["Address"]
```

Here the title asks for the name `Root`, which the definition already holds, so the document became `RootModel`;
`root_class` follows the document, not the name.

A location is the `source_ref` that [`--emit-model-metadata`](cli-reference/base-options.md#emit-model-metadata)
writes: a document and a JSON Pointer (escaped as RFC 6901 says, not percent-encoded). The dictionary you pass is the
empty document, so its locations start with `#`; a file that a `$ref` reads is named by the path dcg resolved, such as
`sub/other.json#/$defs/X` or `whole.json#`, and a URL without its credentials. `$id` and `$anchor` targets are keyed
by their pointer (`#/$defs/User`), not by the URI.

When several classes come from one location, the location keys the class parsed under the location itself, which is
the class a `$ref` to it names. These locations have no key:

- A location whose classes were all made as helpers of another schema, such as the Enum and the `RootModel` of a
  nullable enum property, or the members of a union inside `allOf`, and the `Request` and `Response` classes of
  `read_only_write_only_model_type="request-response"`. With `"all"`, the location keys the base class.
- A location whose class dcg merged into another class: duplicates under `reuse_model` with `collapse_reuse_models`,
  and the targets of `collapse_root_models`.
- The subclasses that `reuse_model` makes, which `--emit-model-metadata` does not list either.
- A location whose model is a type alias (`use_type_alias`), not a class, also when helper classes of it share the
  location.

`root_class` is `None` for OpenAPI and AsyncAPI documents, under `skip_root_model`, and when the document became a
type alias (`use_type_alias`). In split output, `models` holds one of two classes that share a name in different
modules, while `models_by_source_ref` keys both.

## Caching

Models are cached by schema content, configuration, and `module_name` to avoid regeneration.
`target_model_names` only filters the returned dictionary, so it does not change the cache key:

```python
from datamodel_code_generator import generate_dynamic_models, clear_dynamic_models_cache

schema = {"type": "object", "properties": {"x": {"type": "integer"}}}

# First call generates models
models1 = generate_dynamic_models(schema)

# Second call returns cached models (same object)
models2 = generate_dynamic_models(schema)
assert models1 is models2  # True

# Disable caching for specific call
models3 = generate_dynamic_models(schema, cache_size=0)
assert models1 is not models3  # True

# Clear all cached models
cleared = clear_dynamic_models_cache()
print(f"Cleared {cleared} cached schemas")
```

`generate_dynamic_module()` uses the same cache, `cache_size` and `clear_dynamic_models_cache()`, in entries of its
own: its `models` is not the dictionary `generate_dynamic_models()` returns for the same arguments, and holds other
classes.

## Formatting

Dynamic generation formats the code with the formatters of the config, as `generate()` does: Black and isort unless
`formatters`, `custom_formatters` or a preset selects others. Formatting takes about half to two thirds of the time of
a call. Nobody reads the code, so you can pass `formatters=[]`:

```python
from datamodel_code_generator import GenerateConfig, generate_dynamic_models

models = generate_dynamic_models(schema, config=GenerateConfig(formatters=[]))
```

Then Black and isort are not imported, and the "Default formatters will change to builtin" `FutureWarning` is not
shown. The classes are the same but for one thing: formatters strip the spaces and tabs at the ends of the lines of a
class docstring, and without a formatter they stay. Class docstrings come from schema descriptions under
`use_schema_description`, so a description whose lines end with spaces or tabs, such as a Markdown hard break (two
spaces at the end of a line), gives another `__doc__`, and another `description` in `model_json_schema()`, which
Pydantic takes from the docstring.

## Thread Safety

`generate_dynamic_models()` is thread-safe. Multiple threads can safely call it concurrently:

```python
import threading
from datamodel_code_generator import generate_dynamic_models

schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
results = []


def worker():
    models = generate_dynamic_models(schema)
    results.append(models)


threads = [threading.Thread(target=worker) for _ in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()

# All threads get the same cached models
assert all(r is results[0] for r in results)
```

## Limitations

| Limitation | Details |
|------------|---------|
| Pydantic v2 only | Pydantic v2 is the only supported output |
| Not pickle-able | Use `model_dump()` to serialize instances |
| Dict input only | Schema must be a `dict`, not a file path or string |

## Comparison with `generate()`

| Feature | `generate()` | `generate_dynamic_models()` |
|---------|-------------|----------------------------|
| Output | Source code string | Actual Python classes |
| Use case | Code generation, file output | Runtime validation |
| Caching | No | Yes (configurable) |
| Thread-safe | Yes | Yes |
| Pydantic v2 | Yes | Yes |

## See Also

- [Using as Module](using_as_module.md) - `generate()` function reference
- [JSON Schema](jsonschema.md) - JSON Schema examples
- [OpenAPI](openapi.md) - OpenAPI examples
