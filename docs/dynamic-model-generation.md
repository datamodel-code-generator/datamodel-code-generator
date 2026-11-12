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

!!! warning "Experimental"

    `generate_dynamic_module()`, `DynamicModule` and `register_module=True` are experimental. Their parameters,
    attributes and registration rules may change. See [Experimental Features](experimental.md).
    `generate_dynamic_models()` is not experimental.

```python
def generate_dynamic_module(
    input_: Mapping[str, Any],
    *,
    config: GenerateConfig | None = None,
    cache_size: int = 128,
    module_name: str | None = None,
    register_module: bool = False,
) -> DynamicModule:
```

Generates the classes like `generate_dynamic_models()` and also tells which class the document itself became and
which class each schema location became. The parameters are those of `generate_dynamic_models()` without
`target_model_names`, and:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `register_module` | `bool` | `False` | Keep the module of the classes registered in `sys.modules`, so that pickle finds them. Without `module_name`, the name is derived from the schema and the config. See [Picklable Models](#picklable-models-registered-modules) |

See [Root Class and Schema Locations](#root-class-and-schema-locations).

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

!!! warning "Experimental"

    `generate_dynamic_module()`, `DynamicModule` and `register_module=True` are experimental. Their parameters,
    attributes and registration rules may change. See [Experimental Features](experimental.md).
    `generate_dynamic_models()` is not experimental.

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
the class a `$ref` to it names. A duplicate that `reuse_model` writes as an empty subclass of the class it reuses keys
that subclass. These locations have no key:

- A location whose classes were all made as helpers of another schema, such as the Enum and the `RootModel` of a
  nullable enum property, or the members of a union inside `allOf`, and the `Request` and `Response` classes of
  `read_only_write_only_model_type="request-response"`. With `"all"`, the location keys the base class.
- A location whose class dcg merged into another class: duplicates under `reuse_model` with `collapse_reuse_models`,
  and the targets of `collapse_root_models`.
- A location whose model is a type alias (`use_type_alias`), not a class, also when helper classes of it share the
  location.

`root_class` is `None` for OpenAPI and AsyncAPI documents, under `skip_root_model`, and when the document became a
type alias (`use_type_alias`). In split output, `models` holds one of two classes that share a name in different
modules, while `models_by_source_ref` keys both.

## Picklable Models (Registered Modules)

!!! warning "Experimental"

    `register_module=True` is experimental. Its registration rules may change. See
    [Experimental Features](experimental.md).

Pickle stores a class, and the class of an instance, by its module and name, and loads them by importing the module.
With `register_module=True`, `generate_dynamic_module()` keeps the module of the classes registered in `sys.modules`,
so their classes and instances pickle, for example to restore checkpoints or to send them to worker processes:

```python
import pickle

from datamodel_code_generator import generate_dynamic_module

schema = {"title": "Task", "type": "object", "properties": {"name": {"type": "string"}}}

module = generate_dynamic_module(schema, register_module=True)
Task = module.root_class
data = pickle.dumps(Task(name="build"))

# In another process, run the same generation first, then load:
module = generate_dynamic_module(schema, register_module=True)
task = pickle.loads(data)
assert type(task) is module.root_class
```

Without `module_name`, the module is named `_dcg_dynamic_` and a digest of the schema and the config, which is the same
in every process that passes an equal schema and config: the keys of the schema in the same order, and the same
settings (the values of set-valued settings in any order). A schema with its keys in another order, a config that dumps
otherwise after a dcg upgrade, or `Path` settings spelled otherwise give another name. To keep one name across such
changes, pass `module_name` yourself, for example your own digest of the schema. A dotted name needs a parent package
that can be imported, because pickle imports it. Like import, registration binds each module below a package to it,
so `package.module` reaches the registered module.

A call with the name of a registered module and the same schema and config returns the registered classes, also with
`cache_size=0` and without `register_module`. A call without `register_module` that passes the name with another schema
or config, or with a schema or config that cannot be serialized as JSON (which no call can be matched to), builds a
second set of classes in a module of that name; they cannot be pickled, because pickle finds the registered classes
under their names. Registered modules are no cache entries: `cache_size`, eviction and
`clear_dynamic_models_cache()` leave them alone, and each one stays, like an imported module, until you remove it from
`sys.modules`. Register a bounded set of schemas, such as the schemas of tools or workers, and remove a module you no
longer need:

```python
import sys

name = module.module_name
for key in [key for key in sys.modules if key == name or key.startswith(f"{name}.")]:
    del sys.modules[key]
parent, _, leaf = name.rpartition(".")
if parent:
    vars(sys.modules[parent]).pop(leaf, None)
```

After that, the same call registers new classes; pickles of the old instances no longer load into the old classes.

`register_module=True` raises `datamodel_code_generator.Error` and replaces nothing when:

- the name holds the classes of another schema or other settings;
- the name holds classes registered for a schema or config that cannot be serialized as JSON, or this call's cannot be,
  so the two cannot be compared: keep the `DynamicModule` the registering call returned;
- the name holds a module that `generate_dynamic_module()` did not register;
- `sys.modules` holds modules below the name, for example the modules of split output whose package alone was
  removed: remove the modules the message names;
- a module of the name can be imported, which the registered module would hide;
- the parent package of a dotted name cannot be imported;
- no `module_name` is given and the schema or the config cannot be serialized as JSON, for example a config with
  `custom_class_name_generator`.

What does not work:

- Loading in a process that has not run the same generation fails with `ModuleNotFoundError`. Workers started with
  the `spawn` or `forkserver` method (the Linux default since Python 3.14) do not inherit `sys.modules`: run the
  generation in their initializer, and before loading in other threads.
- Files and URLs that `$ref`s read are not part of the derived name, and can differ between processes.
- After a dcg upgrade that renames or changes classes, loading fails or gives instances with stale fields, as Pydantic
  does not validate pickled state. For long-lived storage, use `model_dump()` and `model_validate()`.
- Classes from `generate_dynamic_models()` cannot be pickled, as it does not look at registered modules. A
  `generate_dynamic_module()` call without `register_module=True` returns the classes of a registered module of its
  `module_name` for the same schema and config, which pickle; otherwise its classes cannot be pickled either.
- Under `use_root_model_type_alias`, Pydantic shares one class per parametrization in the process, so an alias class
  belongs to the module that made that parametrization first.

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

## Schemas That Arrive at Runtime

A schema that comes from a user, the network or a database is input you do not control. For such schemas:

- Pass `untrusted_input=True`, so the schema cannot make dcg read files or fetch URLs, or make the generated module
  import other modules. See [Untrusted Input](#untrusted-input). The default is unchanged: without it, dcg reads
  file and URL `$ref`s and honors `x-python-type`, `customTypePath` and `x-python-import`.
- Pass `formatters=[]`, because nobody reads the code. See [Formatting](#formatting).
- Keep `cache_size` above `0`, so a schema that arrives again is not generated again. See [Caching](#caching).
- Catch `datamodel_code_generator.Error`, which a refused or invalid schema raises. A schema nested thousands of levels
  deep can raise `RecursionError` instead, so catch it too where any client can send a schema.

```python
from datamodel_code_generator import Error, GenerateConfig, generate_dynamic_models

config = GenerateConfig(class_name="Payload", untrusted_input=True, formatters=[])

try:
    models = generate_dynamic_models({"$ref": "other.json#/$defs/X"}, config=config)
except (Error, RecursionError) as error:
    print(error)  # Reading the $ref document other.json is refused under --untrusted-input. ...

models = generate_dynamic_models({"type": "object", "properties": {"a": {"type": "string"}}}, config=config)
Payload = models["Payload"]
```

To check documents against the schema as closely as possible, see [Matching the Schema Closely](#matching-the-schema-closely).

## Formatting

Dynamic generation formats the code with the formatters of the config, as `generate()` does: Black and isort unless
`formatters`, `custom_formatters` or a preset selects others. Formatting takes about half to two thirds of the time of
a call: on one machine, a call took 8 ms with the default formatters and 3 ms without for a schema of 5 properties, and
45 ms and 20 ms for a schema of 32 properties with `$defs` and `oneOf` (medians of 15 interleaved runs, `cache_size=0`;
your numbers differ). Nobody reads the code, so you can pass `formatters=[]`:

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

## Untrusted Input

!!! warning "Experimental"
    The references and schema keywords that `untrusted_input` refuses may grow as new ways for a schema to read
    files or write code are found.

A schema that comes from a source you do not control, such as an HTTP request or the tool schema of an LLM, can make
dcg read files and fetch URLs through `$ref`, and make the generated module import other modules through keywords such
as `x-python-import`. Executing that module runs those imports. Set `untrusted_input` (`--untrusted-input` on the
command line, `generate(..., untrusted_input=True)`) to keep such a schema to data:

```python
from datamodel_code_generator import GenerateConfig, generate_dynamic_models

models = generate_dynamic_models(schema, config=GenerateConfig(untrusted_input=True, formatters=[]))
```

- dcg reads no file or URL but the input: a `$ref` must name a location in the input (a JSON pointer, an anchor, an
  embedded `$id` resource, or another file of a directory or file-list input), not another file or a URL; an XML
  Schema includes or imports only files of the input. A reference that would read another document stops generation
  with an error that names it.
- The keywords whose values the generated module imports or evaluates as Python code, `x-python-type`,
  `x-python-import`, `customTypePath` and `customBasePath`, stop generation with an error that names the keyword.
- Protocol Buffers input, whose imports `protoc` reads by itself, and `--validation` are refused.
- It implies `allow_remote_refs=False`. A schema that does none of this generates the same classes.

Descriptions, titles, defaults, examples, enum values and property names are written as escaped literals, comments or
sanitized names, never as code. The switch does not sandbox the generated code, and options you pass yourself, such as
`custom_template_dir`, `extra_template_data`, `additional_imports` or `custom_formatters`, are trusted.

## Matching the Schema Closely

By default, a model accepts some documents the schema rejects. To reject more of them, use a dated
[preset](presets.md), `generate_schema_validators=True` and `use_missing_sentinel=True`. Name the preset for the Python
that runs the models.

```python
from pydantic import ValidationError

from datamodel_code_generator import GenerateConfig, generate_dynamic_models

schema = {
    "type": "object",
    "properties": {
        "count": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
    },
    "minProperties": 1,
}


def build(**options):
    config = GenerateConfig(class_name="Item", formatters=[], preset="standard-py313-20261111", **options)
    return generate_dynamic_models(schema, config=config)["Item"]


def accepts(model, document):
    try:
        model.model_validate(document)
    except ValidationError:
        return False
    return True


plain = build()
close = build(generate_schema_validators=True, use_missing_sentinel=True)

assert accepts(plain, {"count": None}) and not accepts(close, {"count": None})
assert accepts(plain, {}) and not accepts(close, {})
assert accepts(plain, {"tags": ["a", "a"]}) and not accepts(close, {"tags": ["a", "a"]})
assert close.model_validate({"count": 3}).model_dump() == {"count": 3}
```

- `generate_schema_validators` checks rules that a type cannot express, such as `minProperties`, `uniqueItems` and
  required-only `oneOf` groups (see [`--generate-schema-validators`](cli-reference/template-customization.md#generate-schema-validators)).
- `use_missing_sentinel` types an optional property that is not nullable as `T | MISSING`, so `null` is rejected and a
  property left out stays out of `model_dump()`. Without it, such a property is `T | None = None` and takes `null`.
- Do not add `strict_types` for this. It rejects JSON `1.0` for an integer, which the schema accepts.

Both options are experimental, and `use_missing_sentinel` needs Pydantic 2.12 or later. See
[Experimental Features](experimental.md).

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
| Pickling | Only for classes of a registered module (`generate_dynamic_module()` with `register_module=True`, or a later call with its name, schema and config), after the same generation in the loading process; otherwise use `model_dump()` |
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
