<!-- related-cli-options: --model-name-map, --infer-union-variant-names, --use-tag-as-union-variant-name, --extra-fields, --type-mappings, --output-datetime-class, --disable-future-imports, --use-subclass-enum, --use-specialized-enum, --skip-root-model, --custom-file-header, --custom-file-header-mode, --disable-timestamp, --base-class, --base-class-map, --use-single-value-enum-as-const, --allof-merge-mode, --distribute-allof-over-unions, --openapi-scopes, --openapi-include-paths -->

# 🔁 Options That Replace Post-Processing

Many projects rewrite the generated code afterwards (with `sed`, regular expressions or an AST pass), or rewrite the
input schema before generation, to get a form the default output does not have. Most of these rewrites have an
option. This page lists them by the rewrite: find what your script does, and replace it with the option.

Each option is listed with the first release that has it. The examples use
`--output-model-type pydantic_v2.BaseModel --disable-timestamp`; the other output model types take the same options.

| Your script does this | Use | Since |
|---|---|---|
| [Renames numbered classes (`Payload1`, `Payload2`) to stable names](#numbered-class-names) | `--infer-union-variant-names`, `--use-tag-as-union-variant-name`, `--model-name-map` | 0.65.0, 0.86.0, 0.65.0 |
| [Injects a `title` into each union branch of the schema to name it](#numbered-class-names) | `--use-tag-as-union-variant-name` | 0.86.0 |
| [Switches `extra='forbid'` / `'ignore'` / `'allow'`](#extra-fields) | `--extra-fields` | 0.31.0 (Pydantic v2 output: 0.31.1) |
| [Removes `additionalProperties: false` from the schema](#extra-fields) | `--extra-fields ignore` | 0.31.0 (Pydantic v2 output: 0.31.1) |
| [Replaces `AnyUrl`, `Base64Str` or `AwareDatetime` with `str` or `datetime`](#looser-types-for-formats) | `--type-mappings`, `--output-datetime-class` | 0.37.0, 0.26.2 |
| [Deletes `from __future__ import annotations`](#future-imports) | `--disable-future-imports` | 0.34.0 |
| [Turns `class X(Enum)` into `class X(str, Enum)` or `StrEnum`](#enum-base-classes) | `--use-subclass-enum`, `--use-specialized-enum` | 0.13.0 or older, 0.36.0 |
| [Deletes the empty `RootModel[Any]` of a schema that only holds definitions](#root-model-of-a-definitions-file) | `--skip-root-model` | 0.37.0 |
| [Replaces the header comment or adds `# ruff: noqa`](#file-header) | `--custom-file-header`, `--custom-file-header-mode`, `--disable-timestamp` | 0.17.2, 0.69.0, 0.13.0 or older |
| [Replaces `BaseModel` with a project base class](#base-classes) | `--base-class`, `--base-class-map` | 0.13.0 or older, 0.48.0 |
| [Flattens a `oneOf` of single-value enums into one enum](#single-value-enum-branches) | `--use-single-value-enum-as-const` | 0.86.0 |
| [Flattens `allOf` or copies shared properties into union branches](#allof-merging) | `--allof-merge-mode`, `--distribute-allof-over-unions` | 0.44.0, 0.90.0 |
| [Removes the parts of an OpenAPI document you do not use](#only-the-parts-you-use) | `--openapi-scopes`, `--openapi-include-paths` | 0.13.0 or older, 0.52.0 |

## Numbered class names

Inline `oneOf`/`anyOf` branches have no name of their own, so they are numbered after the field:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Event",
  "type": "object",
  "properties": {
    "payload": {
      "oneOf": [
        {"type": "object", "properties": {"type": {"const": "click"}, "x": {"type": "integer"}}, "required": ["type"]},
        {"type": "object", "properties": {"type": {"const": "key"}, "code": {"type": "string"}}, "required": ["type"]}
      ]
    }
  }
}
```

```python
class Payload(BaseModel):
    type: Literal["click"]
    x: int | None = None


class Payload1(BaseModel):
    type: Literal["key"]
    code: str | None = None


class Event(BaseModel):
    payload: Payload | Payload1 | None = None
```

`--infer-union-variant-names` names each branch after the value of its tag field:

```python
class PayloadClick(BaseModel):
    type: Literal["click"]
    x: int | None = None


class PayloadKey(BaseModel):
    type: Literal["key"]
    code: str | None = None


class Event(BaseModel):
    payload: PayloadClick | PayloadKey | None = None
```

`--use-tag-as-union-variant-name` does the same, skips tags whose class names would collide, and also names
single-branch unions and branches with a single required key, which is what a script that copies the tag into each
branch's `title` usually works around. With
`"approval": {"oneOf": [{"enum": ["never", "always"]}, {"properties": {"granular": {...}}, "required": ["granular"]}]}`,
the object branch becomes `ApprovalGranular` instead of `Approval1`.

For names no rule produces, `--model-name-map` renames classes by their schema reference or by the name they would
get:

```bash
datamodel-codegen --input event.json --model-name-map '{"Payload": "ClickPayload", "Payload1": "KeyPayload"}'
```

```python
class Event(BaseModel):
    payload: ClickPayload | KeyPayload | None = None
```

See [`--infer-union-variant-names`](cli-reference/field-customization.md#infer-union-variant-names),
[`--use-tag-as-union-variant-name`](cli-reference/field-customization.md#use-tag-as-union-variant-name) and
[`--model-name-map`](cli-reference/model-customization.md#model-name-map).

## Extra fields

A schema with `additionalProperties: false` writes `extra='forbid'`. `--extra-fields` sets `allow`, `ignore` or
`forbid` on every model instead, so there is no need to remove `additionalProperties: false` from the schema or to
edit the `model_config` afterwards:

```python
# default
class User(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )


# --extra-fields ignore
class User(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
    )
```

See [`--extra-fields`](cli-reference/field-customization.md#extra-fields).

## Looser types for formats

`format` values become strict types: `uri` is `AnyUrl`, `byte` is `Base64Str` and `date-time` is `AwareDatetime`. A
payload that does not meet them is rejected, which is not always what a schema author meant. `--type-mappings`
maps a format (or a `type+format` pair) to another type, and `--output-datetime-class` picks the date-time class:

```bash
datamodel-codegen --input user.json --type-mappings uri=string byte=string --output-datetime-class datetime
```

```python
# default
class User(BaseModel):
    homepage: AnyUrl | None = None
    avatar: Base64Str | None = None
    created: AwareDatetime | None = None


# with the options
class User(BaseModel):
    homepage: str | None = None
    avatar: str | None = None
    created: datetime | None = None
```

See [Type Mappings and Custom Types](type-mappings.md),
[`--type-mappings`](cli-reference/typing-customization.md#type-mappings) and
[`--output-datetime-class`](cli-reference/typing-customization.md#output-datetime-class).

## Future imports

`--disable-future-imports` leaves out `from __future__ import annotations`, for code that reads annotations at run
time:

```python
# default
from __future__ import annotations

from enum import Enum

# --disable-future-imports
from enum import Enum
```

See [`--disable-future-imports`](cli-reference/typing-customization.md#disable-future-imports).

## Enum base classes

A string enum is a plain `Enum` by default. `--use-subclass-enum` adds the value type as a base, and
`--use-specialized-enum` uses `StrEnum` or `IntEnum` (it needs `--target-python-version 3.11` or later):

```python
# default
class Color(Enum):
    red = "red"
    green = "green"


# --use-subclass-enum
class Color(str, Enum):
    red = "red"
    green = "green"


# --use-specialized-enum --target-python-version 3.11
class Color(StrEnum):
    red = "red"
    green = "green"
```

See [`--use-subclass-enum`](cli-reference/model-customization.md#use-subclass-enum) and
[`--use-specialized-enum`](cli-reference/typing-customization.md#use-specialized-enum).

## Root model of a definitions file

A schema whose root only holds `$defs` gets a root model of `Any`:

```python
class Definitions(RootModel[Any]):
    root: Any = Field(..., title="Definitions")


class Pet(BaseModel):
    name: str | None = None
```

`--skip-root-model` leaves the root model out and keeps the definitions:

```python
class Pet(BaseModel):
    name: str | None = None
```

It leaves out the root model whatever the root holds, together with the models only the root defines inline, so use
it for schemas whose root is a container of definitions.

See [`--skip-root-model`](cli-reference/model-customization.md#skip-root-model).

## File header

`--custom-file-header` replaces the `# generated by datamodel-codegen` header, and `--custom-file-header-mode prepend`
puts your lines above it instead. `--disable-timestamp` leaves the timestamp out of the generated header:

```bash
datamodel-codegen --input color.json --custom-file-header '# ruff: noqa' --custom-file-header-mode prepend --disable-timestamp
```

```python
# ruff: noqa
#
# generated by datamodel-codegen:
#   filename:  color.json

from __future__ import annotations
```

See [`--custom-file-header`](cli-reference/template-customization.md#custom-file-header),
[`--custom-file-header-mode`](cli-reference/template-customization.md#custom-file-header-mode) and
[`--disable-timestamp`](cli-reference/template-customization.md#disable-timestamp).

## Base classes

`--base-class` replaces the base of every model, and `--base-class-map` sets it per model:

```bash
datamodel-codegen --input pets.json --base-class-map '{"Pet": "myapp.base.Animal"}'
```

```python
from myapp.base import Animal
from pydantic import BaseModel


class Pet(Animal):
    name: str | None = None


class Owner(BaseModel):
    pets: list[Pet] | None = None
```

See [`--base-class`](cli-reference/model-customization.md#base-class) and
[`--base-class-map`](cli-reference/model-customization.md#base-class-map).

## Single-value enum branches

A `oneOf` of single-value enums, a common way to describe each value, becomes one enum per value:

```json
"priority": {
  "oneOf": [
    {"type": "string", "enum": ["low"], "description": "Low priority"},
    {"type": "string", "enum": ["high"], "description": "High priority"}
  ]
}
```

```python
# default
class Priority(Enum):
    low = "low"


class Priority1(Enum):
    high = "high"


# --use-single-value-enum-as-const
class Priority(Enum):
    low = "low"
    high = "high"
```

See [`--use-single-value-enum-as-const`](cli-reference/typing-customization.md#use-single-value-enum-as-const).

## allOf merging

`--allof-merge-mode` sets what a property of an `allOf` takes from the same property of its parent: `constraints`
(the default), `all` (also `default` and `examples`) or `none`:

```json
"Base": {"type": "object", "properties": {"name": {"type": "string", "minLength": 1, "default": "anon"}}},
"Child": {"allOf": [{"$ref": "#/$defs/Base"}, {"properties": {"name": {"maxLength": 10}}}]}
```

```python
# --allof-merge-mode constraints (default)
class Child(Base):
    name: constr(min_length=1, max_length=10) | None = None


# --allof-merge-mode all
class Child(Base):
    name: constr(min_length=1, max_length=10) | None = "anon"
```

`--distribute-allof-over-unions` gives each `$ref` branch of a union the `allOf` beside it, as JSON Schema validates
it, instead of dropping the `allOf`:

```json
"pet": {"allOf": [{"$ref": "#/$defs/Base"}], "oneOf": [{"$ref": "#/$defs/Cat"}, {"$ref": "#/$defs/Dog"}]}
```

```python
# default
class Model(BaseModel):
    pet: Cat | Dog | None = None


# --distribute-allof-over-unions
class Pet(Cat, Base):
    pass


class Pet1(Dog, Base):
    pass


class Model(BaseModel):
    pet: Pet | Pet1 | None = None
```

See [`--allof-merge-mode`](cli-reference/typing-customization.md#allof-merge-mode) and
[`--distribute-allof-over-unions`](cli-reference/typing-customization.md#distribute-allof-over-unions).

## Only the parts you use

An OpenAPI document generates every schema under `components/schemas` by default. `--openapi-scopes paths` generates
the models the operations use instead, and `--openapi-include-paths` keeps only the operations whose paths match:

```bash
datamodel-codegen --input api.yaml --openapi-scopes paths --openapi-include-paths '/users/*'
```

```python
class User(BaseModel):
    name: str | None = None
```

Without the options, the same document also generates `Order` (used only by `/orders`) and `Unused`.
`--openapi-include-paths` filters the operations only: with `--openapi-scopes schemas paths`, every component schema
is still generated.

See [OpenAPI Options](openapi-options.md),
[`--openapi-scopes`](cli-reference/openapi-only-options.md#openapi-scopes) and
[`--openapi-include-paths`](cli-reference/openapi-only-options.md#openapi-include-paths).
