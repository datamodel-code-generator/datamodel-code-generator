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
- Leave `formatters` unset: nobody reads the code, so dynamic generation does not format it unless you ask. See
  [Formatting](#formatting).
- Keep `cache_size` above `0`, so a schema that arrives again is not generated again. See [Caching](#caching).
- Catch `datamodel_code_generator.Error`, which a refused or invalid schema raises. A schema nested thousands of levels
  deep can raise `RecursionError` instead, so catch it too where any client can send a schema.

```python
from datamodel_code_generator import Error, GenerateConfig, generate_dynamic_models

config = GenerateConfig(class_name="Payload", untrusted_input=True)

try:
    models = generate_dynamic_models({"$ref": "other.json#/$defs/X"}, config=config)
except (Error, RecursionError) as error:
    print(error)  # Reading the $ref document other.json is refused under --untrusted-input. ...

models = generate_dynamic_models({"type": "object", "properties": {"a": {"type": "string"}}}, config=config)
Payload = models["Payload"]
```

To check documents against the schema as closely as possible, see [Matching the Schema Closely](#matching-the-schema-closely).

## LLM Tool and MCP Schemas

The `inputSchema` and `outputSchema` of an MCP tool, and the `parameters` of a function-calling tool, are JSON Schema
documents. Turn each into a model once, validate what the language model sends, and read the typed result. dcg handles
what hand-written converters often lose: `$defs` and `$ref`, a `description` beside a `$ref`, nested inline objects,
enums and tuples. These schemas come from servers you do not control, so pass `untrusted_input=True`. See
[Schemas That Arrive at Runtime](#schemas-that-arrive-at-runtime).

### Validating the Arguments of a Tool Call

```python
from pydantic import ValidationError

from datamodel_code_generator import GenerateConfig, generate_dynamic_module

input_schema = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short summary", "minLength": 1, "maxLength": 80},
        "priority": {"$ref": "#/$defs/Priority", "description": "How urgent the ticket is"},
        "assignee": {"type": ["string", "null"], "description": "User to assign"},
        "labels": {"type": "array", "items": {"type": "string"}, "default": []},
        "reporter-id": {"type": "integer", "minimum": 1},
        "contact": {"$ref": "#/$defs/Contact"},
    },
    "required": ["title", "priority"],
    "additionalProperties": False,
    "$defs": {
        "Priority": {"type": "string", "enum": ["low", "high"]},
        "Contact": {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]},
    },
}

config = GenerateConfig(class_name="CreateTicketInput", untrusted_input=True)
module = generate_dynamic_module(input_schema, config=config)
CreateTicketInput = module.root_class

raw_arguments = '{"title": "Login fails", "priority": "high", "reporter-id": 7}'
call = CreateTicketInput.model_validate_json(raw_arguments)
assert call.priority.value == "high"
assert call.assignee is None
assert call.reporter_id == 7
assert call.model_dump(mode="json", by_alias=True, exclude_none=True) == {
    "title": "Login fails",
    "priority": "high",
    "labels": [],
    "reporter-id": 7,
}

for arguments, location, kind in [
    ({"title": "", "priority": "high"}, ("title",), "string_too_short"),
    ({"title": "x", "priority": "urgent"}, ("priority",), "enum"),
    ({"title": "x", "priority": "low", "contact": {}}, ("contact", "email"), "missing"),
    ({"title": "x", "priority": "low", "colour": "red"}, ("colour",), "extra_forbidden"),
]:
    try:
        CreateTicketInput.model_validate(arguments)
    except ValidationError as error:
        assert [(e["loc"], e["type"]) for e in error.errors()] == [(location, kind)]
    else:
        raise AssertionError(arguments)
```

Use `module.root_class` for the class of the schema itself: it is the right class whatever the schema's `title` is.
`class_name` names it; without `class_name`, the `title` does, and without both, it is `Model`.

The errors name the property and the rule, so you can send them back to the language model as the result of a failed
tool call. Without `"additionalProperties": False` in the schema, a property the schema does not list is ignored.
`extra_fields="forbid"` in the config rejects it for schemas that do not say so.

An optional property is nullable in the model, as the next sections show: `assignee` above is `None` when left out,
and so is `contact`.

### Class Names Follow the Schema

The root class is named by `class_name`, else by the `title`. A `$defs` key, a property that holds an inline object or
an enum, and a `title` give the other classes. The names reach the language model when you send
`model_json_schema()` back, because `$defs` there is keyed by class name:

```python
from datamodel_code_generator import GenerateConfig, generate_dynamic_module

schema = {
    "title": "Create Ticket",
    "type": "object",
    "properties": {
        "priority": {"$ref": "#/$defs/priority-level"},
        "contact": {"type": "object", "title": "Contact Info", "properties": {"email": {"type": "string"}}},
        "kind": {"type": "string", "enum": ["bug", "task"]},
    },
    "$defs": {"priority-level": {"type": "string", "enum": ["low", "high"]}},
}

module = generate_dynamic_module(schema, config=GenerateConfig(untrusted_input=True))
assert sorted(module.models) == ["Contact", "CreateTicket", "Kind", "PriorityLevel"]
assert sorted(module.root_class.model_json_schema()["$defs"]) == ["Contact", "Kind", "PriorityLevel"]

config = GenerateConfig(untrusted_input=True, use_title_as_name=True)
module = generate_dynamic_module(schema, config=config)
assert sorted(module.models) == ["ContactInfo", "CreateTicket", "Kind", "PriorityLevel"]
```

- A `$defs` key that is not an identifier is made into one: `priority-level` becomes `PriorityLevel`.
- The `title` of an inline object is not used by default, so the object is named after its property (`Contact`).
  `use_title_as_name=True` uses it (`ContactInfo`).
- A `title` on a `$defs` entry is not used as a name by default either: the key is.

`custom_class_name_generator` takes a function of the name dcg derived from the schema, the `title`, `$defs` key or
property name as written (`Create Ticket`, `priority-level`, `contact`), and returns the class name, which must be a
valid identifier:

```python
import re

from datamodel_code_generator import GenerateConfig, generate_dynamic_module

schema = {
    "title": "Create Ticket",
    "type": "object",
    "properties": {"priority": {"$ref": "#/$defs/priority-level"}},
    "$defs": {"priority-level": {"type": "string", "enum": ["low", "high"]}},
}


def tool_name(name: str) -> str:
    return "Tool" + "".join(part[:1].upper() + part[1:] for part in re.split(r"[^0-9A-Za-z]+", name))


config = GenerateConfig(untrusted_input=True, custom_class_name_generator=tool_name)
module = generate_dynamic_module(schema, config=config)
assert sorted(module.models) == ["ToolCreateTicket", "ToolPriorityLevel"]
assert module.root_class.__name__ == "ToolCreateTicket"
```

A config with `custom_class_name_generator` can register a module only under a `module_name` you give. See [Picklable Models](#picklable-models-registered-modules).

### Property Names That Are Not Identifiers

A property such as `reporter-id` or `class` becomes a field with a valid name, `reporter_id` or `class_`, and keeps
the schema's name as its alias. The alias is the wire name, which the language model uses:

```python
from datamodel_code_generator import GenerateConfig, generate_dynamic_models

schema = {"type": "object", "properties": {"reporter-id": {"type": "integer"}, "class": {"type": "string"}}}

Plain = generate_dynamic_models(schema, config=GenerateConfig(class_name="Args", untrusted_input=True))["Args"]
assert {name: field.alias for name, field in Plain.model_fields.items()} == {
    "reporter_id": "reporter-id",
    "class_": "class",
}

arguments = Plain.model_validate({"reporter-id": 7, "class": "bug"})
assert arguments.reporter_id == 7 and arguments.class_ == "bug"
assert arguments.model_dump(by_alias=True) == {"reporter-id": 7, "class": "bug"}
assert arguments.model_dump() == {"reporter_id": 7, "class_": "bug"}

# The field name is not accepted: the property is left out, not rejected.
assert Plain.model_validate({"reporter_id": 7}).reporter_id is None
```

Validate with the wire name and dump with `by_alias=True` for what you send back or onward to a tool. `model_json_schema()`
lists the wire names. To accept the field name too, set `allow_population_by_field_name=True`, which sets Pydantic's
`populate_by_name`:

```python
from datamodel_code_generator import GenerateConfig, generate_dynamic_models

schema = {"type": "object", "properties": {"reporter-id": {"type": "integer"}}}
config = GenerateConfig(class_name="Args", untrusted_input=True, allow_population_by_field_name=True)
Both = generate_dynamic_models(schema, config=config)["Args"]
assert Both.model_validate({"reporter-id": 7}).reporter_id == 7
assert Both.model_validate({"reporter_id": 7}).reporter_id == 7
```

Without the option, a model neither rejects nor reports the field name of a schema that does not forbid extra
properties, so a language model that sends `reporter_id` loses the value silently. Use `extra_fields="forbid"` or
`allow_population_by_field_name=True` according to which you want.

### What `model_json_schema()` Gives Back

Sending the schema of the model to a language model, or comparing it with the original, does not give the original
back. These results are measured for the models of this page, with `untrusted_input=True` and no other option, with
Pydantic 2.14. Do not rely on a round trip in general: keep the original schema where the
exact document matters.

| Schema | `model_json_schema()` |
|--------|-----------------------|
| `description` and `title` of a property | Kept |
| `description` (and `title`) beside a `$ref` | Kept on the property, beside the `$ref` |
| `enum`, `const`, and `type` lists with `"null"` | Kept. An enum is a `$defs` entry named after its property |
| `minLength`, `maxLength`, `pattern`, `minimum`, `maximum`, `multipleOf`, `exclusiveMinimum`, `exclusiveMaximum`, `minItems`, `maxItems` | Kept. An exclusive bound `0` reads `0.0` |
| `format`, `examples`, `deprecated` | Kept. `format: uri` also gains `minLength: 1` |
| `default` of an optional property | Kept |
| `required` | Kept. A required property with a `default` loses the `default` |
| `additionalProperties` (`true`, `false`, a schema) | Kept; absent stays absent |
| `$defs` names | Class names: `Foo-Bar` is `FooBar`. A title on a `$defs` entry is not kept |
| Inline objects and enums | Moved into `$defs`, named after the property |
| Optional property that is not nullable | Gains `anyOf` with `{"type": "null"}` and `default: null` |
| `description` of the document, of a `$defs` entry or of an enum | Dropped, unless `use_schema_description=True` |
| `title` of the document | The class name |
| `uniqueItems` | Not kept, unless `use_unique_items_as_set=True` (a `set`, which writes `uniqueItems: true`) |
| `readOnly` and other keywords Pydantic has no field for | Not kept, unless `field_include_all_keys=True` (kept in `json_schema_extra`) |
| `nullable` | Not kept: it is an OpenAPI keyword; in JSON Schema use `"type": ["string", "null"]` |
| `prefixItems`, and `items` as a list (draft-07 tuples) | Not kept: a list, with a warning. An open `prefixItems` reads `items: {}`; a closed one and a draft-07 tuple read `items: {"anyOf": [...]}`. With `use_tuple_for_closed_arrays=True`, an array that no other item can follow and whose length bounds fix its length (for example `items: false` with `minItems`) is a `tuple` and keeps `prefixItems` |

```python
from datamodel_code_generator import GenerateConfig, generate_dynamic_module

schema = {
    "type": "object",
    "description": "Create a ticket",
    "properties": {
        "title": {"type": "string", "description": "Short summary", "maxLength": 80},
        "priority": {"$ref": "#/$defs/Priority", "description": "How urgent the ticket is"},
        "note": {"type": "string"},
    },
    "required": ["title", "priority"],
    "additionalProperties": False,
    "$defs": {"Priority": {"type": "string", "enum": ["low", "high"], "description": "Urgency"}},
}

kept = generate_dynamic_module(schema, config=GenerateConfig(class_name="Tool", untrusted_input=True))
result = kept.root_class.model_json_schema()
assert result["properties"]["title"] == {
    "description": "Short summary",
    "maxLength": 80,
    "title": "Title",
    "type": "string",
}
assert result["properties"]["priority"] == {"$ref": "#/$defs/Priority", "description": "How urgent the ticket is"}
assert result["$defs"]["Priority"]["enum"] == ["low", "high"]
assert result["additionalProperties"] is False and result["required"] == ["title", "priority"]
assert result["properties"]["note"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
assert "description" not in result and "description" not in result["$defs"]["Priority"]

config = GenerateConfig(class_name="Tool", untrusted_input=True, use_schema_description=True)
described = generate_dynamic_module(schema, config=config).root_class.model_json_schema()
assert described["description"] == "Create a ticket"
assert described["$defs"]["Priority"]["description"] == "Urgency"
```

### Tool Outputs That Are Not Objects

An `outputSchema` can describe an array or a scalar. `generate_dynamic_module()` gives the class of the document as
`root_class`, a `RootModel` for an array or a scalar, so validate with it and read `.root`:

```python
from pydantic import ValidationError

from datamodel_code_generator import GenerateConfig, generate_dynamic_module

output_schema = {
    "type": "array",
    "items": {"$ref": "#/$defs/Hit"},
    "$defs": {"Hit": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
}
config = GenerateConfig(class_name="SearchOutput", untrusted_input=True)
module = generate_dynamic_module(output_schema, config=config)
assert sorted(module.models) == ["Hit", "SearchOutput"]

result = module.root_class.model_validate([{"id": 1}, {"id": 2}])
assert [hit.id for hit in result.root] == [1, 2]
assert result.model_dump() == [{"id": 1}, {"id": 2}]

try:
    module.root_class.model_validate([{"name": "no id"}])
except ValidationError as error:
    assert error.errors()[0]["loc"] == (0, "id")

text = generate_dynamic_module({"type": "string", "maxLength": 10}, config=config).root_class
assert text.model_validate("short").root == "short"
```

A string schema with `enum` gives an `Enum` class, not a `RootModel`: call it, `root_class("a")`, instead of
`model_validate`. `generate_dynamic_models()` returns the same classes by name.

### Speed

Dynamic generation does not format the code unless you select formatters, and a schema that arrives again is served
from the cache. Keep `cache_size` above `0` (the default is `128`), so a tool list that you convert on every request
generates its models once. On one machine, a first call (`cache_size=0`) for the tool schema of the first example took
8.2 ms (median of 15 runs on a loaded machine; your numbers differ), against 19 ms with Black and isort selected.

## Formatting

Dynamic generation does not format the code when the config selects no formatter: `generate_dynamic_models()` and
`generate_dynamic_module()` execute the code and never show it, and formatting takes between half and three quarters of
the time of a call. On one machine, a call took 8.1 ms with Black and isort and 2.2 ms without for a schema of 5
properties, and 29 ms and 12 ms for a schema of 32 properties with `$defs` and `oneOf` (medians of 45 interleaved
runs, `cache_size=0`; your numbers differ).
Black and isort are not imported and the "Default formatters will change to builtin" `FutureWarning` is not shown.

The config selects formatters when `formatters` or `custom_formatters` is set, or when its `preset` sets them (presets
dated 20260909 or later select `builtin`). Then the code is formatted as `generate()` formats it:

```python
from datamodel_code_generator import Formatter, GenerateConfig, generate_dynamic_models

models = generate_dynamic_models(schema, config=GenerateConfig(formatters=[Formatter.BUILTIN]))
```

To read the source, call `generate()`, which returns it. `generate()` and the command line are unchanged: they format
with Black and isort unless told otherwise.

The classes are the same with and without formatting but for one thing: formatters strip the spaces and tabs at the
ends of the lines of a class docstring, and without a formatter they stay. Class docstrings come from schema
descriptions under `use_schema_description`, so a description whose lines end with spaces or tabs, such as a Markdown
hard break (two spaces at the end of a line), gives another `__doc__`, and another `description` in
`model_json_schema()`, which Pydantic takes from the docstring. Set `formatters` to get the stripped docstring.

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

models = generate_dynamic_models(schema, config=GenerateConfig(untrusted_input=True))
```

- dcg reads no file or URL but the input: a `$ref` must name a location in the input (a JSON pointer, an anchor, an
  embedded `$id` resource, or another file of a directory or file-list input), not another file or a URL; an XML
  Schema includes or imports only files of the input. A reference that would read another document stops generation
  with an error that names it.
- The keywords whose values the generated module imports or evaluates as Python code, `x-python-type`,
  `x-python-import`, `customTypePath` and `customBasePath`, stop generation with an error that names the keyword.
- Protocol Buffers input, whose imports `protoc` reads by itself, and `--validation` are refused.
- A schema that nests objects and arrays more than 100 levels deep, or holds more than 2,000,000 of them (a YAML alias
  counts at each use), is refused before anything reads it, with an error that names the limit and how far the schema
  went. Real schemas stay far below both: the deepest of thousands of public OpenAPI and JSON Schema documents nest 33
  levels, the largest holds about 520,000.
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
