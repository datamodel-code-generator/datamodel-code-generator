# 🔧 Typing Customization

## 📋 Options

| Option | Description |
|--------|-------------|
| [`--allof-class-hierarchy`](#allof-class-hierarchy) | Controls how allOf schemas are represented in the generated ... |
| [`--allof-merge-mode`](#allof-merge-mode) | Merge constraints from root model references in allOf schema... |
| [`--disable-future-imports`](#disable-future-imports) | Prevent automatic addition of __future__ imports in generate... |
| [`--distribute-allof-over-unions`](#distribute-allof-over-unions) | Read an `allOf` with the `anyOf` and `oneOf` it holds or sit... |
| [`--enum-field-as-literal`](#enum-field-as-literal) | Convert all enum fields to Literal types instead of Enum cla... |
| [`--enum-field-as-literal-map`](#enum-field-as-literal-map) | Override enum/literal generation per-field via JSON mapping. |
| [`--filter-literal-values`](#filter-literal-values) | Keep only the values of a `const` or an `enum` that meet the... |
| [`--ignore-enum-constraints`](#ignore-enum-constraints) | Ignore enum constraints and use base string type instead of ... |
| [`--import-overrides`](#import-overrides) | Override modules for generated imports by symbol name. |
| [`--intersect-allof-keywords`](#intersect-allof-keywords) | Combine the keywords of every part of an `allOf`, a `$ref` w... |
| [`--intersect-combined-schema-keywords`](#intersect-combined-schema-keywords) | Combine the keywords beside an `anyOf` or `oneOf` with each ... |
| [`--keep-enum-member-values`](#keep-enum-member-values) | Give an enum class a value base only when it keeps every mem... |
| [`--no-use-closed-typed-dict`](#no-use-closed-typed-dict) | Disable PEP 728 TypedDict closed/extra_items generation. |
| [`--no-use-specialized-enum`](#no-use-specialized-enum) | Disable specialized Enum classes for Python 3.11+ code gener... |
| [`--no-use-standard-collections`](#no-use-standard-collections) | Use typing.Dict/List instead of built-in dict/list for conta... |
| [`--no-use-union-operator`](#no-use-union-operator) | Use Union[X, Y] / Optional[X] instead of X | Y union operato... |
| [`--open-enums`](#open-enums) | Accept the values an enum does not list. |
| [`--output-date-class`](#output-date-class) | Specify date class type for date schema fields. |
| [`--output-datetime-class`](#output-datetime-class) | Specify datetime class type for date-time schema fields. |
| [`--read-allof-parents-in-mro-order`](#read-allof-parents-in-mro-order) | Read the allOf parents of every inherited schema in the orde... |
| [`--read-const-beside-enum`](#read-const-beside-enum) | Read a `const` beside an `enum` as JSON Schema does. |
| [`--read-format-as-annotation`](#read-format-as-annotation) | Read the format of a schema as an annotation, as JSON Schema... |
| [`--read-kubernetes-extensions`](#read-kubernetes-extensions) | Read the Kubernetes schema extensions as the keywords they s... |
| [`--read-unevaluated-properties`](#read-unevaluated-properties) | Read a schema-valued `unevaluatedProperties` as `additionalP... |
| [`--strict-types`](#strict-types) | Enable strict type validation for specified Python types. |
| [`--type-mappings`](#type-mappings) | Override default type mappings for schema formats. |
| [`--type-overrides`](#type-overrides) | Replace schema model types with custom Python types via JSON... |
| [`--type-overrides-replace-whole-type`](#type-overrides-replace-whole-type) | Make every `--type-overrides` value of a field replace the f... |
| [`--untyped-value-type`](#untyped-value-type) | Write each value the schema leaves untyped as one type inste... |
| [`--use-annotated`](#use-annotated) | Use typing.Annotated for Field() with constraints. |
| [`--use-bytes-for-content-media-type`](#use-bytes-for-content-media-type) | Type an OpenAPI string with a `contentMediaType` and no `con... |
| [`--use-closed-typed-dict`](#use-closed-typed-dict) | Generate TypedDict with PEP 728 closed/extra_items (default:... |
| [`--use-decimal-for-multiple-of`](#use-decimal-for-multiple-of) | Generate Decimal types for fields with multipleOf constraint... |
| [`--use-decimal-in-field-forms`](#use-decimal-in-field-forms) | Write `Decimal` for a number with `multipleOf` in the field ... |
| [`--use-encoded-str-for-constrained-base64`](#use-encoded-str-for-constrained-base64) | Check the constraints of a `format: byte` string on its base... |
| [`--use-generic-container-types`](#use-generic-container-types) | Use generic container types (Sequence, Mapping) for type hin... |
| [`--use-non-positive-negative-number-constrained-types`](#use-non-positive-negative-number-constrained-types) | Use NonPositive/NonNegative types for number constraints. |
| [`--use-object-type`](#use-object-type) | Use object instead of Any for unspecified object and array v... |
| [`--use-pendulum`](#use-pendulum) | Use pendulum types for date, time, and duration fields. |
| [`--use-plain-alias-for-recursive-models`](#use-plain-alias-for-recursive-models) | Write the type alias of a model that names itself as a plain... |
| [`--use-root-model-type-alias`](#use-root-model-type-alias) | Generate RootModel as type alias format for better mypy supp... |
| [`--use-single-dict-for-object-maps`](#use-single-dict-for-object-maps) | Type an object map as one dict instead of a union of dicts. |
| [`--use-single-value-enum-as-const`](#use-single-value-enum-as-const) | Treat `enum` with a single value as `const` with that value. |
| [`--use-specialized-enum`](#use-specialized-enum) | Generate StrEnum/IntEnum for string/integer enums (Python 3.... |
| [`--use-standard-collections`](#use-standard-collections) | Use built-in dict/list instead of typing.Dict/List. |
| [`--use-standard-primitive-types`](#use-standard-primitive-types) | Use Python standard library types for string formats instead... |
| [`--use-strict-str-for-base64`](#use-strict-str-for-base64) | Make a `format: byte` string strict under `--strict-types st... |
| [`--use-total-false-for-typed-dict`](#use-total-false-for-typed-dict) | Generate TypedDict declarations with `total=False`. |
| [`--use-tuple-for-closed-arrays`](#use-tuple-for-closed-arrays) | Generate tuple types for positional arrays that no other ite... |
| [`--use-tuple-for-fixed-items`](#use-tuple-for-fixed-items) | Generate tuple types for arrays with items array syntax. |
| [`--use-tuple-for-fixed-length-arrays`](#use-tuple-for-fixed-length-arrays) | Generate tuple types for homogeneous fixed-length arrays. |
| [`--use-type-alias`](#use-type-alias) | Use TypeAlias instead of root models for type definitions (e... |
| [`--use-type-alias-type`](#use-type-alias-type) | Use runtime TypeAliasType objects for aliases before Python ... |
| [`--use-typed-map-values-beside-other-types`](#use-typed-map-values-beside-other-types) | Type the map values of type arrays that list object beside o... |
| [`--use-typed-nullable-map-values`](#use-typed-nullable-map-values) | Type the values of nullable maps as their schemas say instea... |
| [`--use-union-operator`](#use-union-operator) | Use | operator for Union types (PEP 604). |
| [`--use-unique-items-as-set`](#use-unique-items-as-set) | Generate set types for arrays with uniqueItems constraint. |
| [`--use-unique-items-as-set-by-value`](#use-unique-items-as-set-by-value) | Write a uniqueItems set only where its final items hash by v... |
| [`--use-x-python-import-for-every-ref`](#use-x-python-import-for-every-ref) | Type every reference to a schema with `x-python-import` by t... |
| [`--use-x-python-type-as-written`](#use-x-python-type-as-written) | Write a field's `x-python-type` as given: unions of every sp... |

---

## 🍳 Recipes

### Use modern Python annotations

Target a recent Python version and prefer built-in collection and union syntax in generated types.

**Options:** [`--target-python-version`](model-customization.md#target-python-version), [`--use-union-operator`](#use-union-operator), [`--use-standard-collections`](#use-standard-collections)

### Keep validation constraints in type hints

Combine Annotated hints with strict scalar handling when downstream tooling reads type metadata.

**Options:** [`--use-annotated`](#use-annotated), [`--field-constraints`](field-customization.md#field-constraints), [`--strict-types`](#strict-types)

---

## `--allof-class-hierarchy` {#allof-class-hierarchy}

Controls how allOf schemas are represented in the generated class hierarchy.
`--allof-class-hierarchy if-no-conflict` (default) creates parent classes for allOf schemas
only when there are no property conflicts between parent schemas. Otherwise, properties are merged into the child class
which is then decoupled from the parent classes and no longer inherits from them.
`--allof-class-hierarchy always` keeps class hierarchy for allOf schemas,
even in multiple inheritance scenarios where two parent schemas define the same property.
msgspec.Struct output declares the fields of parent classes whose layouts msgspec cannot combine instead of
inheriting from them.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input allof_root_model_constraints.json --allof-class-hierarchy always # (1)!
    ```

    1. :material-arrow-left: `--allof-class-hierarchy` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "definitions": {
        "StringDatatype": {
          "description": "A base string type.",
          "type": "string",
          "pattern": "^\\S(.*\\S)?$"
        },
        "ConstrainedStringDatatype": {
          "description": "A constrained string.",
          "allOf": [
            { "$ref": "#/definitions/StringDatatype" },
            { "type": "string", "minLength": 1, "pattern": "^[A-Z].*" }
          ]
        },
        "IntegerDatatype": {
          "description": "A whole number.",
          "type": "integer"
        },
        "NonNegativeIntegerDatatype": {
          "description": "Non-negative integer.",
          "allOf": [
            { "$ref": "#/definitions/IntegerDatatype" },
            { "minimum": 0 }
          ]
        },
        "BoundedIntegerDatatype": {
          "description": "Integer between 0 and 100.",
          "allOf": [
            { "$ref": "#/definitions/IntegerDatatype" },
            { "minimum": 0, "maximum": 100 }
          ]
        },
        "EmailDatatype": {
          "description": "Email with format.",
          "allOf": [
            { "$ref": "#/definitions/StringDatatype" },
            { "format": "email" }
          ]
        },
        "FormattedStringDatatype": {
          "description": "A string with email format.",
          "type": "string",
          "format": "email"
        },
        "ObjectBase": {
          "type": "object",
          "properties": {
            "id": { "type": "integer" }
          }
        },
        "ObjectWithAllOf": {
          "description": "Object inheritance - not a root model.",
          "allOf": [
            { "$ref": "#/definitions/ObjectBase" },
            { "type": "object", "properties": { "name": { "type": "string" } } }
          ]
        },
        "MultiRefAllOf": {
          "description": "Multiple refs - not handled by new code.",
          "allOf": [
            { "$ref": "#/definitions/StringDatatype" },
            { "$ref": "#/definitions/IntegerDatatype" }
          ]
        },
        "NoConstraintAllOf": {
          "description": "No constraints added.",
          "allOf": [
            { "$ref": "#/definitions/StringDatatype" }
          ]
        },
        "IncompatibleTypeAllOf": {
          "description": "Incompatible types.",
          "allOf": [
            { "$ref": "#/definitions/StringDatatype" },
            { "type": "boolean" }
          ]
        },
        "ConstraintWithProperties": {
          "description": "Constraint item has properties.",
          "allOf": [
            { "$ref": "#/definitions/StringDatatype" },
            { "properties": { "extra": { "type": "string" } } }
          ]
        },
        "ConstraintWithItems": {
          "description": "Constraint item has items.",
          "allOf": [
            { "$ref": "#/definitions/StringDatatype" },
            { "items": { "type": "string" } }
          ]
        },
        "NumberIntegerCompatible": {
          "description": "Number and integer are compatible.",
          "allOf": [
            { "$ref": "#/definitions/IntegerDatatype" },
            { "type": "number", "minimum": 0 }
          ]
        },
        "RefWithSchemaKeywords": {
          "description": "Ref with additional schema keywords.",
          "allOf": [
            { "$ref": "#/definitions/StringDatatype", "minLength": 5 },
            { "maxLength": 100 }
          ]
        },
        "ArrayDatatype": {
          "type": "array",
          "items": { "type": "string" }
        },
        "RefToArrayAllOf": {
          "description": "Ref to array - not a root model.",
          "allOf": [
            { "$ref": "#/definitions/ArrayDatatype" },
            { "minItems": 1 }
          ]
        },
        "ObjectNoPropsDatatype": {
          "type": "object"
        },
        "RefToObjectNoPropsAllOf": {
          "description": "Ref to object without properties - not a root model.",
          "allOf": [
            { "$ref": "#/definitions/ObjectNoPropsDatatype" },
            { "minProperties": 1 }
          ]
        },
        "PatternPropsDatatype": {
          "patternProperties": {
            "^S_": { "type": "string" }
          }
        },
        "RefToPatternPropsAllOf": {
          "description": "Ref to patternProperties - not a root model.",
          "allOf": [
            { "$ref": "#/definitions/PatternPropsDatatype" },
            { "minProperties": 1 }
          ]
        },
        "NestedAllOfDatatype": {
          "allOf": [
            { "type": "string" },
            { "minLength": 1 }
          ]
        },
        "RefToNestedAllOfAllOf": {
          "description": "Ref to nested allOf root model.",
          "allOf": [
            { "$ref": "#/definitions/NestedAllOfDatatype" },
            { "maxLength": 100 }
          ]
        },
        "ConstraintsOnlyDatatype": {
          "description": "Constraints only, no type.",
          "minLength": 1,
          "pattern": "^[A-Z]"
        },
        "RefToConstraintsOnlyAllOf": {
          "description": "Ref to constraints-only schema.",
          "allOf": [
            { "$ref": "#/definitions/ConstraintsOnlyDatatype" },
            { "maxLength": 100 }
          ]
        },
        "NoDescriptionAllOf": {
          "allOf": [
            { "$ref": "#/definitions/StringDatatype" },
            { "minLength": 5 }
          ]
        },
        "EmptyConstraintItemAllOf": {
          "description": "AllOf with empty constraint item.",
          "allOf": [
            { "$ref": "#/definitions/StringDatatype" },
            {},
            { "maxLength": 50 }
          ]
        },
        "ConflictingFormatAllOf": {
          "description": "Conflicting formats - falls back to existing behavior.",
          "allOf": [
            { "$ref": "#/definitions/FormattedStringDatatype" },
            { "format": "date-time" }
          ]
        }
      },
      "type": "object",
      "properties": {
        "name": { "$ref": "#/definitions/ConstrainedStringDatatype" },
        "count": { "$ref": "#/definitions/NonNegativeIntegerDatatype" },
        "percentage": { "$ref": "#/definitions/BoundedIntegerDatatype" },
        "email": { "$ref": "#/definitions/EmailDatatype" },
        "obj": { "$ref": "#/definitions/ObjectWithAllOf" },
        "multi": { "$ref": "#/definitions/MultiRefAllOf" },
        "noconstraint": { "$ref": "#/definitions/NoConstraintAllOf" },
        "incompatible": { "$ref": "#/definitions/IncompatibleTypeAllOf" },
        "withprops": { "$ref": "#/definitions/ConstraintWithProperties" },
        "withitems": { "$ref": "#/definitions/ConstraintWithItems" },
        "numint": { "$ref": "#/definitions/NumberIntegerCompatible" },
        "refwithkw": { "$ref": "#/definitions/RefWithSchemaKeywords" },
        "refarr": { "$ref": "#/definitions/RefToArrayAllOf" },
        "refobjnoprops": { "$ref": "#/definitions/RefToObjectNoPropsAllOf" },
        "refpatternprops": { "$ref": "#/definitions/RefToPatternPropsAllOf" },
        "refnestedallof": { "$ref": "#/definitions/RefToNestedAllOfAllOf" },
        "refconstraintsonly": { "$ref": "#/definitions/RefToConstraintsOnlyAllOf" },
        "nodescription": { "$ref": "#/definitions/NoDescriptionAllOf" },
        "emptyconstraint": { "$ref": "#/definitions/EmptyConstraintItemAllOf" },
        "conflictingformat": { "$ref": "#/definitions/ConflictingFormatAllOf" }
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  allof_class_hierarchy.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, Field, constr


        class Entity(BaseModel):
            type: str
            type_list: list[str] | None = ['playground:Entity']


        class Entity2(BaseModel):
            type: str
            type_list: list[str]


        class Thing(Entity):
            type: str
            type_list: list[str] | None = ['playground:Thing']
            name: constr(min_length=1) = Field(..., description='The things name')


        class Location(Entity2):
            type: str
            type_list: list[str]
            address: constr(min_length=5) = Field(
                ..., description='The address of the location'
            )


        class Person(Thing, Location):
            name: constr(min_length=1) = Field(..., description="The person's name")
            type: str
            type_list: list[str]
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  allof_class_hierarchy.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Any

        from pydantic import BaseModel, Field, constr


        class Person(BaseModel):
            name: constr(min_length=1) = Field(..., description='The things name')
            type: Any | None = 'playground:Location'
            type_list: list[Any] | None = [
                'playground:Person',
                'playground:Thing',
                'playground:Location',
            ]
            address: constr(min_length=5) = Field(
                ..., description='The address of the location'
            )


        class Entity(BaseModel):
            type: str
            type_list: list[str] | None = ['playground:Entity']


        class Entity2(BaseModel):
            type: str
            type_list: list[str]


        class Thing(Entity):
            type: str
            type_list: list[str] | None = ['playground:Thing']
            name: constr(min_length=1) = Field(..., description='The things name')


        class Location(Entity2):
            type: str
            type_list: list[str]
            address: constr(min_length=5) = Field(
                ..., description='The address of the location'
            )
        ```

        <!-- fmt: on -->

---

## `--allof-merge-mode` {#allof-merge-mode}

Merge constraints from root model references in allOf schemas.

The `--allof-merge-mode constraints` merges only constraint properties
(minLength, maximum, etc.) from parent schemas referenced in allOf.
This ensures child schemas inherit validation constraints while keeping
other properties separate.

**See also:** [Options That Replace Post-Processing](../post-processing-options.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input allof_root_model_constraints.json --allof-merge-mode constraints # (1)!
    ```

    1. :material-arrow-left: `--allof-merge-mode` - the option documented here

??? example "Examples"

    === "OpenAPI"

        **Input Schema:**

        ```yaml
        openapi: "3.0.0"
        info:
          title: Test materialize allOf defaults
          version: "1.0.0"
        components:
          schemas:
            Parent:
              type: object
              properties:
                name:
                  type: string
                  default: "parent_default"
                  minLength: 1
                count:
                  type: integer
                  default: 10
                  minimum: 0
            Child:
              allOf:
                - $ref: "#/components/schemas/Parent"
                - type: object
                  properties:
                    name:
                      maxLength: 100
                    count:
                      maximum: 1000
        ```

        **Output:**

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  allof_materialize_defaults.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, conint, constr


        class Parent(BaseModel):
            name: constr(min_length=1) | None = 'parent_default'
            count: conint(ge=0) | None = 10


        class Child(Parent):
            name: constr(min_length=1, max_length=100) | None = 'parent_default'
            count: conint(ge=0, le=1000) | None = 10
        ```

        <!-- fmt: on -->

    === "JSON Schema"

        **Input Schema:**

        ```json
        {
          "$schema": "http://json-schema.org/draft-07/schema#",
          "definitions": {
            "StringDatatype": {
              "description": "A base string type.",
              "type": "string",
              "pattern": "^\\S(.*\\S)?$"
            },
            "ConstrainedStringDatatype": {
              "description": "A constrained string.",
              "allOf": [
                { "$ref": "#/definitions/StringDatatype" },
                { "type": "string", "minLength": 1, "pattern": "^[A-Z].*" }
              ]
            },
            "IntegerDatatype": {
              "description": "A whole number.",
              "type": "integer"
            },
            "NonNegativeIntegerDatatype": {
              "description": "Non-negative integer.",
              "allOf": [
                { "$ref": "#/definitions/IntegerDatatype" },
                { "minimum": 0 }
              ]
            },
            "BoundedIntegerDatatype": {
              "description": "Integer between 0 and 100.",
              "allOf": [
                { "$ref": "#/definitions/IntegerDatatype" },
                { "minimum": 0, "maximum": 100 }
              ]
            },
            "EmailDatatype": {
              "description": "Email with format.",
              "allOf": [
                { "$ref": "#/definitions/StringDatatype" },
                { "format": "email" }
              ]
            },
            "FormattedStringDatatype": {
              "description": "A string with email format.",
              "type": "string",
              "format": "email"
            },
            "ObjectBase": {
              "type": "object",
              "properties": {
                "id": { "type": "integer" }
              }
            },
            "ObjectWithAllOf": {
              "description": "Object inheritance - not a root model.",
              "allOf": [
                { "$ref": "#/definitions/ObjectBase" },
                { "type": "object", "properties": { "name": { "type": "string" } } }
              ]
            },
            "MultiRefAllOf": {
              "description": "Multiple refs - not handled by new code.",
              "allOf": [
                { "$ref": "#/definitions/StringDatatype" },
                { "$ref": "#/definitions/IntegerDatatype" }
              ]
            },
            "NoConstraintAllOf": {
              "description": "No constraints added.",
              "allOf": [
                { "$ref": "#/definitions/StringDatatype" }
              ]
            },
            "IncompatibleTypeAllOf": {
              "description": "Incompatible types.",
              "allOf": [
                { "$ref": "#/definitions/StringDatatype" },
                { "type": "boolean" }
              ]
            },
            "ConstraintWithProperties": {
              "description": "Constraint item has properties.",
              "allOf": [
                { "$ref": "#/definitions/StringDatatype" },
                { "properties": { "extra": { "type": "string" } } }
              ]
            },
            "ConstraintWithItems": {
              "description": "Constraint item has items.",
              "allOf": [
                { "$ref": "#/definitions/StringDatatype" },
                { "items": { "type": "string" } }
              ]
            },
            "NumberIntegerCompatible": {
              "description": "Number and integer are compatible.",
              "allOf": [
                { "$ref": "#/definitions/IntegerDatatype" },
                { "type": "number", "minimum": 0 }
              ]
            },
            "RefWithSchemaKeywords": {
              "description": "Ref with additional schema keywords.",
              "allOf": [
                { "$ref": "#/definitions/StringDatatype", "minLength": 5 },
                { "maxLength": 100 }
              ]
            },
            "ArrayDatatype": {
              "type": "array",
              "items": { "type": "string" }
            },
            "RefToArrayAllOf": {
              "description": "Ref to array - not a root model.",
              "allOf": [
                { "$ref": "#/definitions/ArrayDatatype" },
                { "minItems": 1 }
              ]
            },
            "ObjectNoPropsDatatype": {
              "type": "object"
            },
            "RefToObjectNoPropsAllOf": {
              "description": "Ref to object without properties - not a root model.",
              "allOf": [
                { "$ref": "#/definitions/ObjectNoPropsDatatype" },
                { "minProperties": 1 }
              ]
            },
            "PatternPropsDatatype": {
              "patternProperties": {
                "^S_": { "type": "string" }
              }
            },
            "RefToPatternPropsAllOf": {
              "description": "Ref to patternProperties - not a root model.",
              "allOf": [
                { "$ref": "#/definitions/PatternPropsDatatype" },
                { "minProperties": 1 }
              ]
            },
            "NestedAllOfDatatype": {
              "allOf": [
                { "type": "string" },
                { "minLength": 1 }
              ]
            },
            "RefToNestedAllOfAllOf": {
              "description": "Ref to nested allOf root model.",
              "allOf": [
                { "$ref": "#/definitions/NestedAllOfDatatype" },
                { "maxLength": 100 }
              ]
            },
            "ConstraintsOnlyDatatype": {
              "description": "Constraints only, no type.",
              "minLength": 1,
              "pattern": "^[A-Z]"
            },
            "RefToConstraintsOnlyAllOf": {
              "description": "Ref to constraints-only schema.",
              "allOf": [
                { "$ref": "#/definitions/ConstraintsOnlyDatatype" },
                { "maxLength": 100 }
              ]
            },
            "NoDescriptionAllOf": {
              "allOf": [
                { "$ref": "#/definitions/StringDatatype" },
                { "minLength": 5 }
              ]
            },
            "EmptyConstraintItemAllOf": {
              "description": "AllOf with empty constraint item.",
              "allOf": [
                { "$ref": "#/definitions/StringDatatype" },
                {},
                { "maxLength": 50 }
              ]
            },
            "ConflictingFormatAllOf": {
              "description": "Conflicting formats - falls back to existing behavior.",
              "allOf": [
                { "$ref": "#/definitions/FormattedStringDatatype" },
                { "format": "date-time" }
              ]
            }
          },
          "type": "object",
          "properties": {
            "name": { "$ref": "#/definitions/ConstrainedStringDatatype" },
            "count": { "$ref": "#/definitions/NonNegativeIntegerDatatype" },
            "percentage": { "$ref": "#/definitions/BoundedIntegerDatatype" },
            "email": { "$ref": "#/definitions/EmailDatatype" },
            "obj": { "$ref": "#/definitions/ObjectWithAllOf" },
            "multi": { "$ref": "#/definitions/MultiRefAllOf" },
            "noconstraint": { "$ref": "#/definitions/NoConstraintAllOf" },
            "incompatible": { "$ref": "#/definitions/IncompatibleTypeAllOf" },
            "withprops": { "$ref": "#/definitions/ConstraintWithProperties" },
            "withitems": { "$ref": "#/definitions/ConstraintWithItems" },
            "numint": { "$ref": "#/definitions/NumberIntegerCompatible" },
            "refwithkw": { "$ref": "#/definitions/RefWithSchemaKeywords" },
            "refarr": { "$ref": "#/definitions/RefToArrayAllOf" },
            "refobjnoprops": { "$ref": "#/definitions/RefToObjectNoPropsAllOf" },
            "refpatternprops": { "$ref": "#/definitions/RefToPatternPropsAllOf" },
            "refnestedallof": { "$ref": "#/definitions/RefToNestedAllOfAllOf" },
            "refconstraintsonly": { "$ref": "#/definitions/RefToConstraintsOnlyAllOf" },
            "nodescription": { "$ref": "#/definitions/NoDescriptionAllOf" },
            "emptyconstraint": { "$ref": "#/definitions/EmptyConstraintItemAllOf" },
            "conflictingformat": { "$ref": "#/definitions/ConflictingFormatAllOf" }
          }
        }
        ```

        **Output:**

        === "With Option"

            <!-- fmt: off -->

            ```python
            # generated by datamodel-codegen:
            #   filename:  allof_root_model_constraints.json
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from typing import Any

            from pydantic import BaseModel, ConfigDict, EmailStr, Field, RootModel, conint, constr


            class StringDatatype(RootModel[constr(pattern=r'^\S(.*\S)?$')]):
                root: constr(pattern=r'^\S(.*\S)?$') = Field(..., description='A base string type.')


            class ConstrainedStringDatatype(RootModel[str]):
                model_config = ConfigDict(
                    regex_engine="python-re",
                )
                root: constr(pattern=r'(?=^\S(.*\S)?$)(?=^[A-Z].*)', min_length=1) = Field(
                    ..., description='A constrained string.'
                )


            class IntegerDatatype(RootModel[int]):
                root: int = Field(..., description='A whole number.')


            class NonNegativeIntegerDatatype(RootModel[conint(ge=0)]):
                root: conint(ge=0) = Field(..., description='Non-negative integer.')


            class BoundedIntegerDatatype(RootModel[conint(ge=0, le=100)]):
                root: conint(ge=0, le=100) = Field(..., description='Integer between 0 and 100.')


            class EmailDatatype(RootModel[EmailStr]):
                root: EmailStr = Field(..., description='Email with format.')


            class FormattedStringDatatype(RootModel[EmailStr]):
                root: EmailStr = Field(..., description='A string with email format.')


            class ObjectBase(BaseModel):
                id: int | None = None


            class ObjectWithAllOf(ObjectBase):
                name: str | None = None


            class MultiRefAllOf(BaseModel):
                pass


            class NoConstraintAllOf(RootModel[StringDatatype]):
                root: StringDatatype = Field(..., description='No constraints added.')


            class IncompatibleTypeAllOf(BaseModel):
                pass


            class ConstraintWithProperties(BaseModel):
                extra: str | None = None


            class ConstraintWithItems(RootModel[StringDatatype]):
                root: StringDatatype = Field(..., description='Constraint item has items.')


            class NumberIntegerCompatible(RootModel[conint(ge=0)]):
                root: conint(ge=0) = Field(..., description='Number and integer are compatible.')


            class RefWithSchemaKeywords(
                RootModel[constr(pattern=r'^\S(.*\S)?$', min_length=5, max_length=100)]
            ):
                root: constr(pattern=r'^\S(.*\S)?$', min_length=5, max_length=100) = Field(
                    ..., description='Ref with additional schema keywords.'
                )


            class ArrayDatatype(RootModel[list[str]]):
                root: list[str]


            class RefToArrayAllOf(RootModel[list[str]]):
                root: list[str] = Field(
                    ..., description='Ref to array - not a root model.', min_length=1
                )


            class ObjectNoPropsDatatype(BaseModel):
                pass


            class RefToObjectNoPropsAllOf(ObjectNoPropsDatatype):
                pass


            class PatternPropsDatatype(RootModel[dict[constr(pattern=r'^S_'), str]]):
                root: dict[constr(pattern=r'^S_'), str]


            class RefToPatternPropsAllOf(BaseModel):
                pass


            class NestedAllOfDatatype(RootModel[constr(min_length=1)]):
                root: constr(min_length=1)


            class RefToNestedAllOfAllOf(RootModel[constr(min_length=1, max_length=100)]):
                root: constr(min_length=1, max_length=100) = Field(
                    ..., description='Ref to nested allOf root model.'
                )


            class ConstraintsOnlyDatatype(RootModel[Any]):
                root: Any = Field(..., description='Constraints only, no type.')


            class RefToConstraintsOnlyAllOf(RootModel[Any]):
                root: Any = Field(..., description='Ref to constraints-only schema.')


            class NoDescriptionAllOf(RootModel[constr(pattern=r'^\S(.*\S)?$', min_length=5)]):
                root: constr(pattern=r'^\S(.*\S)?$', min_length=5) = Field(
                    ..., description='A base string type.'
                )


            class EmptyConstraintItemAllOf(
                RootModel[constr(pattern=r'^\S(.*\S)?$', max_length=50)]
            ):
                root: constr(pattern=r'^\S(.*\S)?$', max_length=50) = Field(
                    ..., description='AllOf with empty constraint item.'
                )


            class ConflictingFormatAllOf(BaseModel):
                pass


            class Model(BaseModel):
                name: ConstrainedStringDatatype | None = None
                count: NonNegativeIntegerDatatype | None = None
                percentage: BoundedIntegerDatatype | None = None
                email: EmailDatatype | None = None
                obj: ObjectWithAllOf | None = None
                multi: MultiRefAllOf | None = None
                noconstraint: NoConstraintAllOf | None = None
                incompatible: IncompatibleTypeAllOf | None = None
                withprops: ConstraintWithProperties | None = None
                withitems: ConstraintWithItems | None = None
                numint: NumberIntegerCompatible | None = None
                refwithkw: RefWithSchemaKeywords | None = None
                refarr: RefToArrayAllOf | None = None
                refobjnoprops: RefToObjectNoPropsAllOf | None = None
                refpatternprops: RefToPatternPropsAllOf | None = None
                refnestedallof: RefToNestedAllOfAllOf | None = None
                refconstraintsonly: RefToConstraintsOnlyAllOf | None = None
                nodescription: NoDescriptionAllOf | None = None
                emptyconstraint: EmptyConstraintItemAllOf | None = None
                conflictingformat: ConflictingFormatAllOf | None = None
            ```

            <!-- fmt: on -->

        === "Without Option"

            <!-- fmt: off -->

            ```python
            # generated by datamodel-codegen:
            #   filename:  allof_root_model_constraints.json
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from typing import Any

            from pydantic import BaseModel, EmailStr, Field, RootModel, conint, constr


            class StringDatatype(RootModel[constr(pattern=r'^\S(.*\S)?$')]):
                root: constr(pattern=r'^\S(.*\S)?$') = Field(..., description='A base string type.')


            class ConstrainedStringDatatype(RootModel[constr(pattern=r'^[A-Z].*', min_length=1)]):
                root: constr(pattern=r'^[A-Z].*', min_length=1) = Field(
                    ..., description='A constrained string.'
                )


            class IntegerDatatype(RootModel[int]):
                root: int = Field(..., description='A whole number.')


            class NonNegativeIntegerDatatype(RootModel[conint(ge=0)]):
                root: conint(ge=0) = Field(..., description='Non-negative integer.')


            class BoundedIntegerDatatype(RootModel[conint(ge=0, le=100)]):
                root: conint(ge=0, le=100) = Field(..., description='Integer between 0 and 100.')


            class EmailDatatype(RootModel[EmailStr]):
                root: EmailStr = Field(..., description='Email with format.')


            class FormattedStringDatatype(RootModel[EmailStr]):
                root: EmailStr = Field(..., description='A string with email format.')


            class ObjectBase(BaseModel):
                id: int | None = None


            class ObjectWithAllOf(ObjectBase):
                name: str | None = None


            class MultiRefAllOf(BaseModel):
                pass


            class NoConstraintAllOf(RootModel[StringDatatype]):
                root: StringDatatype = Field(..., description='No constraints added.')


            class IncompatibleTypeAllOf(BaseModel):
                pass


            class ConstraintWithProperties(BaseModel):
                extra: str | None = None


            class ConstraintWithItems(RootModel[StringDatatype]):
                root: StringDatatype = Field(..., description='Constraint item has items.')


            class NumberIntegerCompatible(RootModel[conint(ge=0)]):
                root: conint(ge=0) = Field(..., description='Number and integer are compatible.')


            class RefWithSchemaKeywords(
                RootModel[constr(pattern=r'^\S(.*\S)?$', min_length=5, max_length=100)]
            ):
                root: constr(pattern=r'^\S(.*\S)?$', min_length=5, max_length=100) = Field(
                    ..., description='Ref with additional schema keywords.'
                )


            class ArrayDatatype(RootModel[list[str]]):
                root: list[str]


            class RefToArrayAllOf(RootModel[list[str]]):
                root: list[str] = Field(
                    ..., description='Ref to array - not a root model.', min_length=1
                )


            class ObjectNoPropsDatatype(BaseModel):
                pass


            class RefToObjectNoPropsAllOf(ObjectNoPropsDatatype):
                pass


            class PatternPropsDatatype(RootModel[dict[constr(pattern=r'^S_'), str]]):
                root: dict[constr(pattern=r'^S_'), str]


            class RefToPatternPropsAllOf(BaseModel):
                pass


            class NestedAllOfDatatype(RootModel[constr(min_length=1)]):
                root: constr(min_length=1)


            class RefToNestedAllOfAllOf(RootModel[constr(min_length=1, max_length=100)]):
                root: constr(min_length=1, max_length=100) = Field(
                    ..., description='Ref to nested allOf root model.'
                )


            class ConstraintsOnlyDatatype(RootModel[Any]):
                root: Any = Field(..., description='Constraints only, no type.')


            class RefToConstraintsOnlyAllOf(RootModel[Any]):
                root: Any = Field(..., description='Ref to constraints-only schema.')


            class NoDescriptionAllOf(RootModel[constr(pattern=r'^\S(.*\S)?$', min_length=5)]):
                root: constr(pattern=r'^\S(.*\S)?$', min_length=5) = Field(
                    ..., description='A base string type.'
                )


            class EmptyConstraintItemAllOf(
                RootModel[constr(pattern=r'^\S(.*\S)?$', max_length=50)]
            ):
                root: constr(pattern=r'^\S(.*\S)?$', max_length=50) = Field(
                    ..., description='AllOf with empty constraint item.'
                )


            class ConflictingFormatAllOf(BaseModel):
                pass


            class Model(BaseModel):
                name: ConstrainedStringDatatype | None = None
                count: NonNegativeIntegerDatatype | None = None
                percentage: BoundedIntegerDatatype | None = None
                email: EmailDatatype | None = None
                obj: ObjectWithAllOf | None = None
                multi: MultiRefAllOf | None = None
                noconstraint: NoConstraintAllOf | None = None
                incompatible: IncompatibleTypeAllOf | None = None
                withprops: ConstraintWithProperties | None = None
                withitems: ConstraintWithItems | None = None
                numint: NumberIntegerCompatible | None = None
                refwithkw: RefWithSchemaKeywords | None = None
                refarr: RefToArrayAllOf | None = None
                refobjnoprops: RefToObjectNoPropsAllOf | None = None
                refpatternprops: RefToPatternPropsAllOf | None = None
                refnestedallof: RefToNestedAllOfAllOf | None = None
                refconstraintsonly: RefToConstraintsOnlyAllOf | None = None
                nodescription: NoDescriptionAllOf | None = None
                emptyconstraint: EmptyConstraintItemAllOf | None = None
                conflictingformat: ConflictingFormatAllOf | None = None
            ```

            <!-- fmt: on -->

---

## `--disable-future-imports` {#disable-future-imports}

Prevent automatic addition of __future__ imports in generated code.

The --disable-future-imports option stops the generator from adding
'from __future__ import annotations' to the output. This is useful when
you need compatibility with tools or environments that don't support
postponed evaluation of annotations (PEP 563).

**See also:** [Options That Replace Post-Processing](../post-processing-options.md), [Python Version Compatibility](../python-version-compatibility.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input keep_model_order_field_references.json --disable-future-imports --target-python-version 3.10 # (1)!
    ```

    1. :material-arrow-left: `--disable-future-imports` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "DescriptionType",
      "type": "object",
      "properties": {
        "metadata": {
          "type": "array",
          "items": {
            "$ref": "#/definitions/Metadata"
          }
        }
      },
      "definitions": {
        "Metadata": {
          "title": "Metadata",
          "type": "object",
          "properties": {
            "title": {
              "type": "string"
            }
          }
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  keep_model_order_field_references.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from pydantic import BaseModel


    class Metadata(BaseModel):
        title: str | None = None


    class DescriptionType(BaseModel):
        metadata: list[Metadata] | None = None
    ```

    <!-- fmt: on -->

---

## `--distribute-allof-over-unions` {#distribute-allof-over-unions}

Read an `allOf` with the `anyOf` and `oneOf` it holds or sits beside, as JSON Schema does.

A schema with an `allOf` beside an `anyOf` or `oneOf` admits a value that every `allOf` member and one branch admit.
An inline branch is parsed with the `allOf` merged in, but a `$ref` branch takes only the `properties` and `required`
beside it, so `{allOf: [Base], oneOf: [Cat, Dog]}` writes `Cat | Dog` and drops `Base`. With
`--distribute-allof-over-unions` such a branch gets a class whose bases are the branch and the `allOf` members
(`Pet1(Cat, Base)`), at a definition, a property, an array item and a map value alike, and an OpenAPI request or
response body is that union too (without the option it is parsed by its `allOf` and the union is dropped). A `$ref`
branch that names no object takes the value keywords beside the union, as an inline branch does, so `minimum: 5`
beside `anyOf: [{$ref: Quantity}, {type: string}]` rejects `3`. An `allOf` member's value keywords meet such a branch
under `--use-site-models-for-constrained-refs`, as they meet a `$ref` member of an `allOf` there.

An `allOf` whose members hold several `anyOf` or `oneOf` admits a value that meets one branch of each. Without the
option their branches join one union, each with the other parts (and the inline branches of a later `anyOf` replace
those of an earlier one); with it the `allOf` is one union variant per combination of one branch of each
(`M6(M1, M3, M5)`, a class each branch appears in once), at most 128 per `allOf` (above that the branches join one
union, with a warning). A `$ref` member that names an `allOf` over a union, which was a base that later passes drop, is
read in its place, so its union joins those of the site (`Leaf4(Leaf1, Leaf3)`). The value branches of these unions are
read at a field (`RootModel[constr(min_length=2) | int]` where 0.89.0 writes `Any`), beside a `$ref` member that is
the type of a field or an item, and beside object branches (`R3m3 | constr(min_length=2)`), as an `allOf` of value
unions that would be a model without fields is read without the option.

**See also:** [Options That Replace Post-Processing](../post-processing-options.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input allof_beside_union.json --distribute-allof-over-unions # (1)!
    ```

    1. :material-arrow-left: `--distribute-allof-over-unions` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "Model",
      "type": "object",
      "properties": {
        "pet": {
          "$ref": "#/$defs/Pet"
        },
        "pet_inline_member": {
          "$ref": "#/$defs/PetInlineMember"
        },
        "pet_mixed": {
          "$ref": "#/$defs/PetMixed"
        },
        "pet_inline": {
          "$ref": "#/$defs/PetInline"
        },
        "pet_props": {
          "$ref": "#/$defs/PetProps"
        },
        "pet_field": {
          "allOf": [
            {
              "$ref": "#/$defs/Base"
            }
          ],
          "oneOf": [
            {
              "$ref": "#/$defs/Cat"
            },
            {
              "$ref": "#/$defs/Dog"
            }
          ]
        },
        "items": {
          "type": "array",
          "items": {
            "allOf": [
              {
                "$ref": "#/$defs/Base"
              }
            ],
            "anyOf": [
              {
                "$ref": "#/$defs/Cat"
              },
              {
                "$ref": "#/$defs/Dog"
              }
            ]
          }
        },
        "values": {
          "type": "object",
          "additionalProperties": {
            "allOf": [
              {
                "$ref": "#/$defs/Base"
              }
            ],
            "anyOf": [
              {
                "$ref": "#/$defs/Cat"
              },
              {
                "$ref": "#/$defs/Dog"
              }
            ]
          }
        }
      },
      "$defs": {
        "Base": {
          "type": "object",
          "required": [
            "id"
          ],
          "properties": {
            "id": {
              "type": "string"
            }
          }
        },
        "Cat": {
          "type": "object",
          "required": [
            "meow"
          ],
          "properties": {
            "meow": {
              "type": "boolean"
            }
          }
        },
        "Dog": {
          "type": "object",
          "required": [
            "bark"
          ],
          "properties": {
            "bark": {
              "type": "boolean"
            }
          }
        },
        "Pet": {
          "allOf": [
            {
              "$ref": "#/$defs/Base"
            }
          ],
          "oneOf": [
            {
              "$ref": "#/$defs/Cat"
            },
            {
              "$ref": "#/$defs/Dog"
            }
          ]
        },
        "PetInlineMember": {
          "allOf": [
            {
              "type": "object",
              "required": [
                "id"
              ],
              "properties": {
                "id": {
                  "type": "string"
                }
              }
            }
          ],
          "anyOf": [
            {
              "$ref": "#/$defs/Cat"
            },
            {
              "$ref": "#/$defs/Dog"
            }
          ]
        },
        "PetMixed": {
          "allOf": [
            {
              "$ref": "#/$defs/Base"
            }
          ],
          "anyOf": [
            {
              "$ref": "#/$defs/Cat"
            },
            {
              "type": "object",
              "required": [
                "bark"
              ],
              "properties": {
                "bark": {
                  "type": "boolean"
                }
              }
            }
          ]
        },
        "PetInline": {
          "allOf": [
            {
              "$ref": "#/$defs/Base"
            }
          ],
          "oneOf": [
            {
              "type": "object",
              "required": [
                "meow"
              ],
              "properties": {
                "meow": {
                  "type": "boolean"
                }
              }
            },
            {
              "type": "object",
              "required": [
                "bark"
              ],
              "properties": {
                "bark": {
                  "type": "boolean"
                }
              }
            }
          ]
        },
        "PetProps": {
          "type": "object",
          "required": [
            "id"
          ],
          "properties": {
            "id": {
              "type": "string"
            }
          },
          "oneOf": [
            {
              "$ref": "#/$defs/Cat"
            },
            {
              "$ref": "#/$defs/Dog"
            }
          ]
        }
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  allof_beside_union.json

        from __future__ import annotations

        from pydantic import BaseModel, RootModel


        class Base(BaseModel):
            id: str


        class Cat(BaseModel):
            meow: bool


        class Dog(BaseModel):
            bark: bool


        class Pet1(Cat, Base):
            pass


        class Pet2(Dog, Base):
            pass


        class Pet(RootModel[Pet1 | Pet2]):
            root: Pet1 | Pet2


        class PetInlineMember1(Cat):
            id: str


        class PetInlineMember2(Dog):
            id: str


        class PetInlineMember(RootModel[PetInlineMember1 | PetInlineMember2]):
            root: PetInlineMember1 | PetInlineMember2


        class PetMixed1(Base):
            bark: bool


        class PetMixed2(Cat, Base):
            pass


        class PetMixed(RootModel[PetMixed2 | PetMixed1]):
            root: PetMixed2 | PetMixed1


        class PetInline1(Base):
            meow: bool


        class PetInline2(Base):
            bark: bool


        class PetInline(RootModel[PetInline1 | PetInline2]):
            root: PetInline1 | PetInline2


        class PetProps1(Cat):
            id: str


        class PetProps2(Dog):
            id: str


        class PetProps(RootModel[PetProps1 | PetProps2]):
            root: PetProps1 | PetProps2


        class PetField(Cat, Base):
            pass


        class PetField1(Dog, Base):
            pass


        class Items(Cat, Base):
            pass


        class Items1(Dog, Base):
            pass


        class Values(Cat, Base):
            pass


        class Values1(Dog, Base):
            pass


        class Model(BaseModel):
            pet: Pet | None = None
            pet_inline_member: PetInlineMember | None = None
            pet_mixed: PetMixed | None = None
            pet_inline: PetInline | None = None
            pet_props: PetProps | None = None
            pet_field: PetField | PetField1 | None = None
            items: list[Items | Items1] | None = None
            values: dict[str, Values | Values1] | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  allof_beside_union.json

        from __future__ import annotations

        from pydantic import BaseModel, RootModel


        class Base(BaseModel):
            id: str


        class Cat(BaseModel):
            meow: bool


        class Dog(BaseModel):
            bark: bool


        class Pet(RootModel[Cat | Dog]):
            root: Cat | Dog


        class PetInlineMember(RootModel[Cat | Dog]):
            root: Cat | Dog


        class PetMixed1(Base):
            bark: bool


        class PetMixed(RootModel[Cat | PetMixed1]):
            root: Cat | PetMixed1


        class PetInline1(Base):
            meow: bool


        class PetInline2(Base):
            bark: bool


        class PetInline(RootModel[PetInline1 | PetInline2]):
            root: PetInline1 | PetInline2


        class PetProps1(Cat):
            id: str


        class PetProps2(Dog):
            id: str


        class PetProps(RootModel[PetProps1 | PetProps2]):
            root: PetProps1 | PetProps2


        class Model(BaseModel):
            pet: Pet | None = None
            pet_inline_member: PetInlineMember | None = None
            pet_mixed: PetMixed | None = None
            pet_inline: PetInline | None = None
            pet_props: PetProps | None = None
            pet_field: Cat | Dog | None = None
            items: list[Cat | Dog] | None = None
            values: dict[str, Cat | Dog] | None = None
        ```

        <!-- fmt: on -->

---

## `--enum-field-as-literal` {#enum-field-as-literal}

Convert all enum fields to Literal types instead of Enum classes.

The `--enum-field-as-literal all` flag converts all enum types to Literal
type annotations. This is useful when you want string literal types instead
of Enum classes for all enumerations.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input enums.graphql --enum-field-as-literal all # (1)!
    ```

    1. :material-arrow-left: `--enum-field-as-literal` - the option documented here

??? example "Examples"

    === "OpenAPI"

        **Input Schema:**

        ```yaml
        openapi: "3.0.0"
        info:
          version: 1.0.0
          title: Swagger Petstore
          license:
            name: MIT
        servers:
          - url: http://petstore.swagger.io/v1
        paths:
          /pets:
            get:
              summary: List all pets
              operationId: listPets
              tags:
                - pets
              parameters:
                - name: limit
                  in: query
                  description: How many items to return at one time (max 100)
                  required: false
                  schema:
                    type: integer
                    format: int32
              responses:
                '200':
                  description: A paged array of pets
                  headers:
                    x-next:
                      description: A link to the next page of responses
                      schema:
                        type: string
                  content:
                    application/json:
                      schema:
                        $ref: "#/components/schemas/Pets"
                default:
                  description: unexpected error
                  content:
                    application/json:
                      schema:
                        $ref: "#/components/schemas/Error"
                        x-amazon-apigateway-integration:
                          uri:
                            Fn::Sub: arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${PythonVersionFunction.Arn}/invocations
                          passthroughBehavior: when_no_templates
                          httpMethod: POST
                          type: aws_proxy
        components:
          schemas:
            Pet:
              required:
                - id
                - name
                - number
                - boolean
              properties:
                id:
                  type: integer
                  format: int64
                name:
                  type: string
                tag:
                  type: string
                kind:
                  type: string
                  enum: ['dog', 'cat']
                type:
                  type: string
                  enum: [ 'animal' ]
                number:
                  type: integer
                  enum: [ 1 ]
                boolean:
                  type: boolean
                  enum: [ true ]
        
            Pets:
              type: array
              items:
                $ref: "#/components/schemas/Pet"
            animal:
              type: object
              properties:
                kind:
                  type: string
                  enum: ['snake', 'rabbit']
            Error:
              required:
                - code
                - message
              properties:
                code:
                  type: integer
                  format: int32
                message:
                  type: string
            EnumObject:
              type: object
              properties:
                type:
                  enum: ['a', 'b']
                  type: string
            EnumRoot:
              enum: ['a', 'b']
              type: string
            IntEnum:
              enum: [1,2]
              type: number
            AliasEnum:
              enum: [1,2,3]
              type: number
              x-enum-varnames: ['a', 'b', 'c']
            MultipleTypeEnum:
              enum: [ "red", "amber", "green", null, 42 ]
            singleEnum:
              enum: [ "pet" ]
              type: string
            arrayEnum:
              type: array
              items: [
                { enum: [ "cat" ] },
                { enum: [ "dog"]}
              ]
            nestedNullableEnum:
              type: object
              properties:
                nested_version:
                  type: string
                  nullable: true
                  default: RC1
                  description: nullable enum
                  example: RC2
                  enum:
                    - RC1
                    - RC1N
                    - RC2
                    - RC2N
                    - RC3
                    - RC4
                    - null
            version:
              type: string
              nullable: true
              default: RC1
              description: nullable enum
              example: RC2
              enum:
              - RC1
              - RC1N
              - RC2
              - RC2N
              - RC3
              - RC4
              - null
        ```

        **Output:**

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  enum_models.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum
        from typing import Literal

        from pydantic import BaseModel, Field, RootModel


        class Kind(Enum):
            dog = 'dog'
            cat = 'cat'


        class Pet(BaseModel):
            id: int
            name: str
            tag: str | None = None
            kind: Kind | None = None
            type: Literal['animal'] | None = None
            number: Literal[1]
            boolean: Literal[True]


        class Pets(RootModel[list[Pet]]):
            root: list[Pet]


        class Kind1(Enum):
            snake = 'snake'
            rabbit = 'rabbit'


        class Animal(BaseModel):
            kind: Kind1 | None = None


        class Error(BaseModel):
            code: int
            message: str


        class Type(Enum):
            a = 'a'
            b = 'b'


        class EnumObject(BaseModel):
            type: Type | None = None


        class EnumRoot(Enum):
            a = 'a'
            b = 'b'


        class IntEnum(Enum):
            number_1 = 1
            number_2 = 2


        class AliasEnum(Enum):
            a = 1
            b = 2
            c = 3


        class MultipleTypeEnum(Enum):
            red = 'red'
            amber = 'amber'
            green = 'green'
            NoneType_None = None
            int_42 = 42


        class SingleEnum(RootModel[Literal['pet']]):
            root: Literal['pet']


        class ArrayEnum(RootModel[list[Literal['cat'] | Literal['dog']]]):
            root: list[Literal['cat'] | Literal['dog']]


        class NestedVersionEnum(Enum):
            RC1 = 'RC1'
            RC1N = 'RC1N'
            RC2 = 'RC2'
            RC2N = 'RC2N'
            RC3 = 'RC3'
            RC4 = 'RC4'


        class NestedVersion(RootModel[NestedVersionEnum | None]):
            root: NestedVersionEnum | None = Field(
                'RC1', description='nullable enum', examples=['RC2']
            )


        class NestedNullableEnum(BaseModel):
            nested_version: NestedVersion | None = Field(
                'RC1', description='nullable enum', examples=['RC2'], validate_default=True
            )


        class VersionEnum(Enum):
            RC1 = 'RC1'
            RC1N = 'RC1N'
            RC2 = 'RC2'
            RC2N = 'RC2N'
            RC3 = 'RC3'
            RC4 = 'RC4'


        class Version(RootModel[VersionEnum | None]):
            root: VersionEnum | None = Field(
                'RC1', description='nullable enum', examples=['RC2']
            )
        ```

        <!-- fmt: on -->

    === "JSON Schema"

        **Input Schema:**

        ```yaml
        $schema: http://json-schema.org/draft-07/schema#
        type: object
        title: Config
        properties:
          mode:
            title: Mode
            type: string
            oneOf:
              - title: fast
                const: fast
              - title: slow
                const: slow
          modes:
            type: array
            items:
              type: string
              oneOf:
                - const: a
                - const: b
        ```

        **Output:**

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  oneof_const_enum_nested.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Literal

        from pydantic import BaseModel, Field


        class Config(BaseModel):
            mode: Literal['fast', 'slow'] | None = Field(None, title='Mode')
            modes: list[Literal['a', 'b']] | None = None
        ```

        <!-- fmt: on -->

    === "GraphQL"

        **Input Schema:**

        ```graphql
        "Employee shift status"
        enum EmployeeShiftStatus {
          "not on shift"
          NOT_ON_SHIFT
          "on shift"
          ON_SHIFT
        }
        
        enum Color {
          RED
          GREEN
          BLUE
        }
        
        enum EnumWithOneField {
            FIELD
        }
        ```

        **Output:**

        === "With Option"

            <!-- fmt: off -->

            ```python
            # generated by datamodel-codegen:
            #   filename:  enums.graphql
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from typing import Literal

            from pydantic import RootModel
            from typing_extensions import TypeAliasType

            Boolean = TypeAliasType("Boolean", bool)
            """
            The `Boolean` scalar type represents `true` or `false`.
            """


            String = TypeAliasType("String", str)
            """
            The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
            """


            class Color(RootModel[Literal['BLUE', 'GREEN', 'RED']]):
                root: Literal['BLUE', 'GREEN', 'RED']


            class EmployeeShiftStatus(RootModel[Literal['NOT_ON_SHIFT', 'ON_SHIFT']]):
                """
                Employee shift status
                """

                root: Literal['NOT_ON_SHIFT', 'ON_SHIFT']


            class EnumWithOneField(RootModel[Literal['FIELD']]):
                root: Literal['FIELD']
            ```

            <!-- fmt: on -->

        === "Without Option"

            <!-- fmt: off -->

            ```python
            # generated by datamodel-codegen:
            #   filename:  enums.graphql
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from enum import Enum

            from typing_extensions import TypeAliasType

            Boolean = TypeAliasType("Boolean", bool)
            """
            The `Boolean` scalar type represents `true` or `false`.
            """


            String = TypeAliasType("String", str)
            """
            The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
            """


            class Color(Enum):
                BLUE = 'BLUE'
                GREEN = 'GREEN'
                RED = 'RED'


            class EmployeeShiftStatus(Enum):
                """
                Employee shift status
                """

                NOT_ON_SHIFT = 'NOT_ON_SHIFT'
                ON_SHIFT = 'ON_SHIFT'


            class EnumWithOneField(Enum):
                FIELD = 'FIELD'
            ```

            <!-- fmt: on -->

---

## `--enum-field-as-literal-map` {#enum-field-as-literal-map}

Override enum/literal generation per-field via JSON mapping.

The `--enum-field-as-literal-map` option allows per-field control over whether
to generate Literal types or Enum classes. Overrides `--enum-field-as-literal`.

You can pass the mapping either inline as JSON or as a path to a JSON file.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input enum_field_as_literal_map.json --enum-field-as-literal-map '{"status": "literal"}' # (1)!
    ```

    1. :material-arrow-left: `--enum-field-as-literal-map` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "EnumFieldAsLiteralMap",
      "type": "object",
      "properties": {
        "status": {
          "title": "Status",
          "type": "string",
          "enum": ["active", "inactive", "pending"]
        },
        "priority": {
          "title": "Priority",
          "type": "string",
          "enum": ["high", "medium", "low"]
        },
        "category": {
          "title": "Category",
          "type": "string",
          "enum": ["a", "b", "c"]
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  enum_field_as_literal_map.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum
    from typing import Literal

    from pydantic import BaseModel, Field


    class Priority(Enum):
        high = 'high'
        medium = 'medium'
        low = 'low'


    class Category(Enum):
        a = 'a'
        b = 'b'
        c = 'c'


    class EnumFieldAsLiteralMap(BaseModel):
        status: Literal['active', 'inactive', 'pending'] | None = Field(
            None, title='Status'
        )
        priority: Priority | None = Field(None, title='Priority')
        category: Category | None = Field(None, title='Category')
    ```

    <!-- fmt: on -->

---

## `--filter-literal-values` {#filter-literal-values}

Keep only the values of a `const` or an `enum` that meet the constraints beside them.

JSON Schema validates a value against the `enum` and the keywords beside it, so `{"enum": ["hello", "goodbye"],
"maxLength": 5}` admits `hello` only. With `--filter-literal-values`, the `Literal` or the Enum of a `const` or
an `enum` holds the values that meet the `minLength`, `maxLength`, `minimum`, `maximum`, `exclusiveMinimum`,
`exclusiveMaximum`, `multipleOf` and `pattern` keywords beside them, as far as the generator can decide them (a
`pattern` only where every regular expression engine reads it alike). A schema none of whose values meets them
admits no value: it stays as written, with a warning. A field that writes a constraint itself, such as
`Field(min_length=...)`, keeps the values that constraint checks. `--omit-redundant-constraints` includes this
check. Without the option, every value stays and the keywords are written beside the type.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input max_items_enum.yaml --filter-literal-values # (1)!
    ```

    1. :material-arrow-left: `--filter-literal-values` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: "3.1.0"
    components:
      schemas:
        Foo:
          type: object
          properties:
            bar:
              type: array
              items:
                enum:
                  - hello
                  - goodbye
                maxLength: 5
                minLength: 1
                type: string
                pattern: "^.*$"
              maxItems: 3
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  max_items_enum.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum

    from pydantic import BaseModel, Field


    class BarEnum(Enum):
        hello = 'hello'


    class Foo(BaseModel):
        bar: list[BarEnum] | None = Field(None, max_length=3)
    ```

    <!-- fmt: on -->

---

## `--ignore-enum-constraints` {#ignore-enum-constraints}

Ignore enum constraints and use base string type instead of Enum classes.

The `--ignore-enum-constraints` flag ignores enum constraints and uses
the base type (str) instead of generating Enum classes. This is useful
when you need flexibility in the values a field can accept beyond the
defined enum members.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input enums.graphql --ignore-enum-constraints # (1)!
    ```

    1. :material-arrow-left: `--ignore-enum-constraints` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```graphql
    "Employee shift status"
    enum EmployeeShiftStatus {
      "not on shift"
      NOT_ON_SHIFT
      "on shift"
      ON_SHIFT
    }
    
    enum Color {
      RED
      GREEN
      BLUE
    }
    
    enum EnumWithOneField {
        FIELD
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  enums.graphql
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import RootModel
        from typing_extensions import TypeAliasType

        Boolean = TypeAliasType("Boolean", bool)
        """
        The `Boolean` scalar type represents `true` or `false`.
        """


        String = TypeAliasType("String", str)
        """
        The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
        """


        class Color(RootModel[str]):
            root: str


        class EmployeeShiftStatus(RootModel[str]):
            """
            Employee shift status
            """

            root: str


        class EnumWithOneField(RootModel[str]):
            root: str
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  enums.graphql
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum

        from typing_extensions import TypeAliasType

        Boolean = TypeAliasType("Boolean", bool)
        """
        The `Boolean` scalar type represents `true` or `false`.
        """


        String = TypeAliasType("String", str)
        """
        The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
        """


        class Color(Enum):
            BLUE = 'BLUE'
            GREEN = 'GREEN'
            RED = 'RED'


        class EmployeeShiftStatus(Enum):
            """
            Employee shift status
            """

            NOT_ON_SHIFT = 'NOT_ON_SHIFT'
            ON_SHIFT = 'ON_SHIFT'


        class EnumWithOneField(Enum):
            FIELD = 'FIELD'
        ```

        <!-- fmt: on -->

---

## `--import-overrides` {#import-overrides}

Override modules for generated imports by symbol name.

The mapping applies to imports in generated output regardless of their original module, except for
`__future__` imports. This is useful for routing typing helpers through a project compatibility module
across Python target versions.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input datetime.json --output-model-type typing.TypedDict --target-python-version 3.10 --import-overrides '{"TypedDict": "my_project.typing_compat", "NotRequired": "my_project.typing_compat"}' # (1)!
    ```

    1. :material-arrow-left: `--import-overrides` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "definitions": {
        "User": {
          "type": "object",
          "properties": {
            "name": {
              "type": "string",
              "example": "ken"
            },
            "birthDay": {
              "type": "string",
              "format": "date-time",
              "example": "2016-08-29T09:12:33.001Z"
            }
          }
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  datetime.json
    #   timestamp: 1985-10-26T08:21:00+00:00

    from __future__ import annotations

    from typing import Any, TypeAlias

    from my_project.typing_compat import NotRequired, TypedDict

    Model: TypeAlias = Any


    class User(TypedDict):
        name: NotRequired[str]
        birthDay: NotRequired[str]
    ```

    <!-- fmt: on -->

---

## `--intersect-allof-keywords` {#intersect-allof-keywords}

Combine the keywords of every part of an `allOf`, a `$ref` with siblings or an override.

JSON Schema applies the members of an `allOf`, a `$ref` and the keywords beside it, and a property that overrides
an inherited one all at once, so a value must meet every part. Without the option the keyword of the last part
wins where the parts disagree: the sibling of a `$ref`, the overriding property, the last member of an `allOf`.
With `--intersect-allof-keywords`, the tightest bound of the parts is written (`maximum: 10` beside `maximum: 20`
keeps `10`), an `enum` holds the values every part admits, a `type` the types every part admits, `required` the names
of every part, and the `pattern`s of the parts are checked together. An `allOf` of value schemas at a root, an item
or a map value is one constraint schema in the same way, and a null member beside a `type` that excludes null is
dropped. Parts that no value meets together keep the earlier merge with a warning.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input inherited_overrides.json --intersect-allof-keywords # (1)!
    ```

    1. :material-arrow-left: `--intersect-allof-keywords` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "Model",
      "type": "object",
      "properties": {
        "c": {
          "$ref": "#/$defs/Child"
        }
      },
      "$defs": {
        "Base": {
          "type": "object",
          "properties": {
            "x": {
              "type": "integer",
              "maximum": 10
            },
            "e": {
              "type": "string",
              "enum": [
                "a",
                "b"
              ]
            },
            "y": {
              "type": "integer",
              "minimum": 0
            }
          }
        },
        "Child": {
          "allOf": [
            {
              "$ref": "#/$defs/Base"
            },
            {
              "type": "object",
              "properties": {
                "x": {
                  "maximum": 20
                },
                "e": {
                  "enum": [
                    "b",
                    "c"
                  ]
                },
                "y": {
                  "maximum": 5
                }
              }
            }
          ]
        }
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  inherited_overrides.json

        from __future__ import annotations

        from enum import Enum

        from pydantic import BaseModel, Field


        class E(Enum):
            a = 'a'
            b = 'b'


        class Base(BaseModel):
            x: int | None = Field(None, le=10)
            e: E | None = None
            y: int | None = Field(None, ge=0)


        class E1(Enum):
            b = 'b'


        class Child(Base):
            x: int | None = Field(None, le=10)
            e: E1 | None = None
            y: int | None = Field(None, ge=0, le=5)


        class Model(BaseModel):
            c: Child | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  inherited_overrides.json

        from __future__ import annotations

        from enum import Enum

        from pydantic import BaseModel, Field


        class E(Enum):
            a = 'a'
            b = 'b'


        class Base(BaseModel):
            x: int | None = Field(None, le=10)
            e: E | None = None
            y: int | None = Field(None, ge=0)


        class E1(Enum):
            b = 'b'
            c = 'c'


        class Child(Base):
            x: int | None = Field(None, le=20)
            e: E1 | None = None
            y: int | None = Field(None, ge=0, le=5)


        class Model(BaseModel):
            c: Child | None = None
        ```

        <!-- fmt: on -->

---

## `--intersect-combined-schema-keywords` {#intersect-combined-schema-keywords}

Combine the keywords beside an `anyOf` or `oneOf` with each branch, as JSON Schema does.

The keywords beside an `anyOf` or `oneOf` apply to every value, whichever branch it meets. Without the option each
branch is parsed with them merged in, and the branch's value of a keyword wins: `minimum: 5` beside a branch with
`minimum: 1` gives `ge=1`. With `--intersect-combined-schema-keywords`, which turns on `--intersect-allof-keywords`,
each branch is their intersection as `--intersect-allof-keywords` writes it: the tightest bound, the conjunction of
the `pattern`s, the common `multipleOf`, the `type` and `enum` values both admit, and nested `properties` alike; the
`nullable` beside the `anyOf` stays with the branch. Where parts meet, an `enum` also loses the values the merged
`type` rejects. A branch whose keywords no value meets together keeps its earlier merge with a warning. Where an
`anyOf` and a `oneOf` stand side by side, a branch of one keeps the branches of the other whose `type` admits one of
its types, and a branch that keeps none is left out, unless every branch is. A property that overrides an inherited
one merges its `properties` name by name, so a nested property named `default` or `nullable` stays; an override that
narrows the inherited `type` (to `null`, to one of its types, or in a nested schema) is the intersection instead of
the inherited type; and under `--allof-merge-mode none` the type shape keeps the length of a closed tuple. An `allOf`
member reads the siblings of its `$ref` as a part where the dialect does (2019-09 and later, OpenAPI 3.1), a member
naming a `$ref` alias takes the alias's target as its base, and the union an `allOf` member's `anyOf` or `oneOf` gives
admits null where every part of the `allOf` does.

**Option relationships:**

- **Implies:** [`--intersect-allof-keywords`](typing-customization.md#intersect-allof-keywords) enabled

!!! tip "Usage"

    ```bash
    datamodel-codegen --input combined_branch_keywords.json --intersect-combined-schema-keywords # (1)!
    ```

    1. :material-arrow-left: `--intersect-combined-schema-keywords` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "Model",
      "type": "object",
      "$defs": {
        "Code": {"type": "integer", "minimum": 1},
        "Small": {"type": "integer"},
        "SmallOrNull": {"type": ["integer", "null"]},
        "NoneLeft": {"$ref": "#/$defs/Small", "enum": ["a", "b"]},
        "NullLeft": {"$ref": "#/$defs/SmallOrNull", "enum": [null, "a"]},
        "Nested": {
          "type": "object",
          "properties": {"a": {"type": "integer", "minimum": 5}},
          "anyOf": [
            {"properties": {"a": {"minimum": 1}, "b": {"type": "string"}}, "required": ["b"]},
            {"required": ["a"]}
          ]
        }
      },
      "properties": {
        "bound": {"minimum": 5, "anyOf": [{"type": "integer", "minimum": 1}, {"type": "string"}]},
        "length": {"maxLength": 3, "oneOf": [{"type": "string", "maxLength": 10}, {"type": "integer"}]},
        "pattern": {"pattern": "^a", "anyOf": [{"type": "string", "pattern": "b$"}, {"type": "integer"}]},
        "multiple": {"multipleOf": 2, "anyOf": [{"type": "integer", "multipleOf": 3}, {"type": "string"}]},
        "typed": {"type": "string", "anyOf": [{"type": ["string", "integer"], "minLength": 1}, {"maxLength": 3}]},
        "values": {"enum": ["a", "b", 1], "anyOf": [{"type": "string", "enum": ["b", "c"]}, {"type": "integer"}]},
        "ref_branch": {"anyOf": [{"$ref": "#/$defs/Code", "minimum": 5}, {"type": "string"}]},
        "ref_enum": {"$ref": "#/$defs/Small", "enum": [1, "a", 2], "x-enum-varnames": ["one", "letter", "two"]},
        "dead": {"type": "string", "anyOf": [{"type": "integer"}, {"minLength": 1}]}
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  combined_branch_keywords.json

        from __future__ import annotations

        from enum import Enum, IntEnum

        from pydantic import BaseModel, ConfigDict, RootModel, conint, constr


        class Bound(RootModel[conint(ge=5)]):
            root: conint(ge=5)


        class Bound1(RootModel[str]):
            root: str


        class Length(RootModel[constr(max_length=3)]):
            root: constr(max_length=3)


        class Length1(RootModel[int]):
            root: int


        class Pattern(RootModel[str]):
            model_config = ConfigDict(
                regex_engine="python-re",
            )
            root: constr(pattern=r'\A(?=[\s\S]*?(?:^a))(?=[\s\S]*?(?:b$))')


        class Pattern1(RootModel[int]):
            root: int


        class Multiple(RootModel[conint(multiple_of=6)]):
            root: conint(multiple_of=6)


        class Multiple1(RootModel[str]):
            root: str


        class Values(Enum):
            b = 'b'


        class Values1(IntEnum):
            integer_1 = 1


        class RefEnum(IntEnum):
            one = 1
            two = 2


        class Model(BaseModel):
            bound: Bound | Bound1 | None = None
            length: Length | Length1 | None = None
            pattern: Pattern | Pattern1 | None = None
            multiple: Multiple | Multiple1 | None = None
            typed: constr(min_length=1) | constr(max_length=3) | None = None
            values: Values | Values1 | None = None
            ref_branch: conint(ge=5) | str | None = None
            ref_enum: RefEnum | None = None
            dead: int | constr(min_length=1) | None = None


        class Code(RootModel[conint(ge=1)]):
            root: conint(ge=1)


        class Small(RootModel[int]):
            root: int


        class SmallOrNull(RootModel[int | None]):
            root: int | None


        class NoneLeft(Enum):
            a = 'a'
            b = 'b'


        class NullLeft(Enum):
            NoneType_None = None


        class Nested1(BaseModel):
            a: conint(ge=5) | None = None
            b: str


        class Nested2(BaseModel):
            a: conint(ge=5)


        class Nested(RootModel[Nested1 | Nested2]):
            root: Nested1 | Nested2
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  combined_branch_keywords.json

        from __future__ import annotations

        from enum import Enum

        from pydantic import BaseModel, RootModel, conint, constr


        class Bound(RootModel[conint(ge=1)]):
            root: conint(ge=1)


        class Bound1(RootModel[str]):
            root: str


        class Length(RootModel[constr(max_length=10)]):
            root: constr(max_length=10)


        class Length1(RootModel[int]):
            root: int


        class Pattern(RootModel[constr(pattern=r'b$')]):
            root: constr(pattern=r'b$')


        class Pattern1(RootModel[int]):
            root: int


        class Multiple(RootModel[conint(multiple_of=3)]):
            root: conint(multiple_of=3)


        class Multiple1(RootModel[str]):
            root: str


        class Values(Enum):
            b = 'b'


        class Values1(Enum):
            a = 'a'
            b = 'b'
            integer_1 = 1


        class RefEnum(Enum):
            one = 1
            letter = 'a'
            two = 2


        class Model(BaseModel):
            bound: Bound | Bound1 | None = None
            length: Length | Length1 | None = None
            pattern: Pattern | Pattern1 | None = None
            multiple: Multiple | Multiple1 | None = None
            typed: constr(min_length=1) | int | constr(max_length=3) | None = None
            values: Values | Values1 | None = None
            ref_branch: conint(ge=5) | str | None = None
            ref_enum: RefEnum | None = None
            dead: int | constr(min_length=1) | None = None


        class Code(RootModel[conint(ge=1)]):
            root: conint(ge=1)


        class Small(RootModel[int]):
            root: int


        class SmallOrNull(RootModel[int | None]):
            root: int | None


        class NoneLeft(Enum):
            a = 'a'
            b = 'b'


        class NullLeft(Enum):
            NoneType_None = None
            a = 'a'


        class Nested1(BaseModel):
            a: conint(ge=1) | None = None
            b: str


        class Nested2(BaseModel):
            a: conint(ge=5)


        class Nested(RootModel[Nested1 | Nested2]):
            root: Nested1 | Nested2
        ```

        <!-- fmt: on -->

---

## `--keep-enum-member-values` {#keep-enum-member-values}

Give an enum class a value base only when it keeps every member value.

`--use-specialized-enum` (`IntEnum`, `StrEnum`) and `--use-subclass-enum` (`(T, Enum)`) give an enum class the value
type of its schema type as a base. Without this option the base follows the schema type alone, as in 0.86.0, so a
member the base changes is written with the changed value: const branches `[1, 2.5]` make an `IntEnum` whose `2.5`
becomes `2` (rejecting the schema-valid `2.5` and accepting `2`), and `['a', 1]` under `--use-subclass-enum` a
`(str, Enum)` holding `'1'`. With the option such an enum is a plain `Enum` that keeps the values as written; members
the base keeps, as `1.0` in an `IntEnum` or `1` in a `(float, Enum)`, keep the base. msgspec keeps the 0.86.0 class
where it could not decode the plain `Enum`. A base that cannot hold a member at all, so that the class failed at
import (`'a'` in an `IntEnum`, a `format: byte` enum), gives a plain `Enum` either way.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input enum_value_base_kinds.json --keep-enum-member-values # (1)!
    ```

    1. :material-arrow-left: `--keep-enum-member-values` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "EnumValueBaseKinds",
      "type": "object",
      "properties": {
        "str_then_int": {"oneOf": [{"const": "a"}, {"const": 1}]},
        "int_then_str": {"oneOf": [{"const": 1}, {"const": "a"}]},
        "int_then_float": {"oneOf": [{"const": 1}, {"const": 2.5}]},
        "int_then_bool": {"oneOf": [{"const": 1}, {"const": true}]},
        "numbers": {"type": "number", "enum": [1, 2.5]},
        "integral_floats": {"type": "integer", "enum": [1.0, 2.0]},
        "base64": {"type": "string", "format": "byte", "enum": ["YQ==", "Yg=="]},
        "byte_counts": {"type": "string", "format": "byte", "enum": [1, 2]}
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  enum_value_base_kinds.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum, IntEnum

        from pydantic import BaseModel


        class StrThenInt(Enum):
            a = 'a'
            field_1 = 1


        class IntThenStr(Enum):
            integer_1 = 1
            a = 'a'


        class IntThenFloat(Enum):
            integer_1 = 1
            integer_2_5 = 2.5


        class IntThenBool(IntEnum):
            integer_1 = 1
            integer_True = True


        class Numbers(Enum):
            number_1 = 1
            number_2_5 = 2.5


        class IntegralFloats(IntEnum):
            integer_1_0 = 1.0
            integer_2_0 = 2.0


        class Base64(Enum):
            YQ__ = 'YQ=='
            Yg__ = 'Yg=='


        class ByteCounts(Enum):
            field_1 = 1
            field_2 = 2


        class EnumValueBaseKinds(BaseModel):
            str_then_int: StrThenInt | None = None
            int_then_str: IntThenStr | None = None
            int_then_float: IntThenFloat | None = None
            int_then_bool: IntThenBool | None = None
            numbers: Numbers | None = None
            integral_floats: IntegralFloats | None = None
            base64: Base64 | None = None
            byte_counts: ByteCounts | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  enum_value_base_kinds.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum, IntEnum

        from pydantic import BaseModel


        class StrThenInt(Enum):
            a = 'a'
            field_1 = 1


        class IntThenStr(Enum):
            integer_1 = 1
            a = 'a'


        class IntThenFloat(IntEnum):
            integer_1 = 1
            integer_2_5 = 2.5


        class IntThenBool(IntEnum):
            integer_1 = 1
            integer_True = True


        class Numbers(Enum):
            number_1 = 1
            number_2_5 = 2.5


        class IntegralFloats(IntEnum):
            integer_1_0 = 1.0
            integer_2_0 = 2.0


        class Base64(Enum):
            YQ__ = 'YQ=='
            Yg__ = 'Yg=='


        class ByteCounts(Enum):
            field_1 = 1
            field_2 = 2


        class EnumValueBaseKinds(BaseModel):
            str_then_int: StrThenInt | None = None
            int_then_str: IntThenStr | None = None
            int_then_float: IntThenFloat | None = None
            int_then_bool: IntThenBool | None = None
            numbers: Numbers | None = None
            integral_floats: IntegralFloats | None = None
            base64: Base64 | None = None
            byte_counts: ByteCounts | None = None
        ```

        <!-- fmt: on -->

---

## `--no-use-closed-typed-dict` {#no-use-closed-typed-dict}

Disable PEP 728 TypedDict closed/extra_items generation.

Use this option for compatibility with type checkers that don't yet support PEP 728
(e.g., mypy). TypedDict will be generated without `closed=True` or `extra_items`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input typed_dict_closed.json --output-model-type typing.TypedDict --no-use-closed-typed-dict # (1)!
    ```

    1. :material-arrow-left: `--no-use-closed-typed-dict` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "object",
      "title": "ClosedModel",
      "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"}
      },
      "required": ["name"],
      "additionalProperties": false
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  typed_dict_closed.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import TypedDict

    from typing_extensions import NotRequired


    class ClosedModel(TypedDict):
        name: str
        age: NotRequired[int]
    ```

    <!-- fmt: on -->

---

## `--no-use-specialized-enum` {#no-use-specialized-enum}

Disable specialized Enum classes for Python 3.11+ code generation.

The `--no-use-specialized-enum` flag prevents the generator from using
specialized Enum classes (StrEnum, IntEnum) when generating code for
Python 3.11+, falling back to standard Enum classes instead.

**Related:** [`--target-python-version`](model-customization.md#target-python-version), [`--use-specialized-enum`](#use-specialized-enum), [`--use-subclass-enum`](model-customization.md#use-subclass-enum)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input enums.graphql --target-python-version 3.11 --no-use-specialized-enum # (1)!
    ```

    1. :material-arrow-left: `--no-use-specialized-enum` - the option documented here

??? example "Examples"

    === "OpenAPI"

        **Input Schema:**

        ```json
        {
          "openapi": "3.0.2",
          "components": {
            "schemas": {
              "ProcessingStatus": {
                "title": "ProcessingStatus",
                "enum": [
                  "COMPLETED",
                  "PENDING",
                  "FAILED"
                ],
                "type": "string",
                "description": "The processing status"
              },
              "ProcessingTask": {
                "title": "ProcessingTask",
                "type": "object",
                "properties": {
                  "processing_status": {
                    "title": "Status of the task",
                    "allOf": [
                      {
                        "$ref": "#/components/schemas/ProcessingStatus"
                      }
                    ],
                    "default": "COMPLETED"
                  }
                }
              },
            }
          },
          "info": {
            "title": "",
            "version": ""
          },
          "paths": {}
        }
        ```

        **Output:**

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  subclass_enum.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum

        from pydantic import BaseModel, Field


        class ProcessingStatus(Enum):
            COMPLETED = 'COMPLETED'
            PENDING = 'PENDING'
            FAILED = 'FAILED'


        class ProcessingTask(BaseModel):
            processing_status: ProcessingStatus | None = Field(
                'COMPLETED', title='Status of the task'
            )
        ```

        <!-- fmt: on -->

    === "JSON Schema"

        **Input Schema:**

        ```json
        {
          "$schema": "http://json-schema.org/draft-07/schema#",
          "type": "object",
          "properties": {
            "IntEnum": {
              "type": "integer",
              "enum": [
                1,
                2,
                3
              ]
            },
            "FloatEnum": {
              "type": "number",
              "enum": [
                1.1,
                2.1,
                3.1
              ]
            },
            "StrEnum": {
              "type": "string",
              "enum": [
                "1",
                "2",
                "3"
              ]
            },
            "NonTypedEnum": {
              "enum": [
                "1",
                "2",
                "3"
              ]
            },
            "BooleanEnum": {
              "type": "boolean",
              "enum": [
                true,
                false
              ]
            },
            "UnknownEnum": {
              "type": "unknown",
              "enum": [
                "a",
                "b"
              ]
            }
          }
        }
        ```

        **Output:**

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  subclass_enum.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum

        from pydantic import BaseModel, Field


        class IntEnum(Enum):
            integer_1 = 1
            integer_2 = 2
            integer_3 = 3


        class FloatEnum(Enum):
            number_1_1 = 1.1
            number_2_1 = 2.1
            number_3_1 = 3.1


        class StrEnum(Enum):
            field_1 = '1'
            field_2 = '2'
            field_3 = '3'


        class NonTypedEnum(Enum):
            field_1 = '1'
            field_2 = '2'
            field_3 = '3'


        class BooleanEnum(Enum):
            boolean_True = True
            boolean_False = False


        class UnknownEnum(Enum):
            a = 'a'
            b = 'b'


        class Model(BaseModel):
            IntEnum_1: IntEnum | None = Field(None, alias='IntEnum')
            FloatEnum_1: FloatEnum | None = Field(None, alias='FloatEnum')
            StrEnum_1: StrEnum | None = Field(None, alias='StrEnum')
            NonTypedEnum_1: NonTypedEnum | None = Field(None, alias='NonTypedEnum')
            BooleanEnum_1: BooleanEnum | None = Field(None, alias='BooleanEnum')
            UnknownEnum_1: UnknownEnum | None = Field(None, alias='UnknownEnum')
        ```

        <!-- fmt: on -->

    === "GraphQL"

        **Input Schema:**

        ```graphql
        "Employee shift status"
        enum EmployeeShiftStatus {
          "not on shift"
          NOT_ON_SHIFT
          "on shift"
          ON_SHIFT
        }
        
        enum Color {
          RED
          GREEN
          BLUE
        }
        
        enum EnumWithOneField {
            FIELD
        }
        ```

        **Output:**

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  enums.graphql
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum

        from typing_extensions import TypeAliasType

        Boolean = TypeAliasType("Boolean", bool)
        """
        The `Boolean` scalar type represents `true` or `false`.
        """


        String = TypeAliasType("String", str)
        """
        The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
        """


        class Color(Enum):
            BLUE = 'BLUE'
            GREEN = 'GREEN'
            RED = 'RED'


        class EmployeeShiftStatus(Enum):
            """
            Employee shift status
            """

            NOT_ON_SHIFT = 'NOT_ON_SHIFT'
            ON_SHIFT = 'ON_SHIFT'


        class EnumWithOneField(Enum):
            FIELD = 'FIELD'
        ```

        <!-- fmt: on -->

---

## `--no-use-standard-collections` {#no-use-standard-collections}

Use typing.Dict/List instead of built-in dict/list for container types.

The `--no-use-standard-collections` flag generates typing module containers
(Dict, List) instead of built-in types. This is useful for older Python
versions or when explicit typing imports are preferred.

**Related:** [`--use-standard-collections`](#use-standard-collections)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input root_model_with_additional_properties.json --no-use-standard-collections # (1)!
    ```

    1. :material-arrow-left: `--no-use-standard-collections` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "$id": "test.json",
      "description": "test",
      "type": "object",
      "required": [
        "test_id",
        "test_ip",
        "result",
        "nested_object_result",
        "nested_enum_result"
      ],
      "properties": {
        "test_id": {
          "type": "string",
          "description": "test ID"
        },
        "test_ip": {
          "type": "string",
          "description": "test IP"
        },
        "result": {
          "type": "object",
          "additionalProperties": {
            "type": "integer"
          }
        },
        "nested_object_result": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "properties": {
              "status":{
                "type": "integer"
              }
            },
            "required": ["status"]
          }
        },
        "nested_enum_result": {
          "type": "object",
          "additionalProperties": {
            "enum": ["red", "green"]
          }
        },
        "all_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "allOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "one_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "oneOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "any_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "anyOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "all_of_with_unknown_object" :{
          "type" : "object",
          "additionalProperties" :
          {
            "allOf" : [
              { "$ref" : "#/definitions/User" },
              { "description": "TODO" }
            ]
          }
        },
        "objectRef": {
          "type": "object",
          "additionalProperties": {
            "$ref": "#/definitions/User"
          }
        },
        "deepNestedObjectRef": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "additionalProperties": {
              "type": "object",
              "additionalProperties": {
                 "$ref": "#/definitions/User"
              }
            }
          }
        }
      },
      "definitions": {
        "User": {
          "type": "object",
          "properties": {
            "name": {
              "type": "string"
            }
          }
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  root_model_with_additional_properties.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum
    from typing import Dict

    from pydantic import BaseModel, Field


    class NestedObjectResult(BaseModel):
        status: int


    class NestedEnumResult(Enum):
        red = 'red'
        green = 'green'


    class OneOfResult(BaseModel):
        description: str | None = None


    class AnyOfResult(BaseModel):
        description: str | None = None


    class User(BaseModel):
        name: str | None = None


    class AllOfResult(User):
        description: str | None = None


    class Model(BaseModel):
        test_id: str = Field(..., description='test ID')
        test_ip: str = Field(..., description='test IP')
        result: Dict[str, int]
        nested_object_result: Dict[str, NestedObjectResult]
        nested_enum_result: Dict[str, NestedEnumResult]
        all_of_result: Dict[str, AllOfResult] | None = None
        one_of_result: Dict[str, User | OneOfResult] | None = None
        any_of_result: Dict[str, User | AnyOfResult] | None = None
        all_of_with_unknown_object: Dict[str, User] | None = None
        objectRef: Dict[str, User] | None = None
        deepNestedObjectRef: Dict[str, Dict[str, Dict[str, User]]] | None = None
    ```

    <!-- fmt: on -->

---

## `--no-use-union-operator` {#no-use-union-operator}

Use Union[X, Y] / Optional[X] instead of X | Y union operator.

The `--no-use-union-operator` flag generates union types using typing.Union
and typing.Optional instead of the | operator (PEP 604). This is useful
for older Python versions or when explicit typing imports are preferred.

**Related:** [`--use-union-operator`](#use-union-operator)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input root_model_with_additional_properties.json --no-use-union-operator # (1)!
    ```

    1. :material-arrow-left: `--no-use-union-operator` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "$id": "test.json",
      "description": "test",
      "type": "object",
      "required": [
        "test_id",
        "test_ip",
        "result",
        "nested_object_result",
        "nested_enum_result"
      ],
      "properties": {
        "test_id": {
          "type": "string",
          "description": "test ID"
        },
        "test_ip": {
          "type": "string",
          "description": "test IP"
        },
        "result": {
          "type": "object",
          "additionalProperties": {
            "type": "integer"
          }
        },
        "nested_object_result": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "properties": {
              "status":{
                "type": "integer"
              }
            },
            "required": ["status"]
          }
        },
        "nested_enum_result": {
          "type": "object",
          "additionalProperties": {
            "enum": ["red", "green"]
          }
        },
        "all_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "allOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "one_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "oneOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "any_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "anyOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "all_of_with_unknown_object" :{
          "type" : "object",
          "additionalProperties" :
          {
            "allOf" : [
              { "$ref" : "#/definitions/User" },
              { "description": "TODO" }
            ]
          }
        },
        "objectRef": {
          "type": "object",
          "additionalProperties": {
            "$ref": "#/definitions/User"
          }
        },
        "deepNestedObjectRef": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "additionalProperties": {
              "type": "object",
              "additionalProperties": {
                 "$ref": "#/definitions/User"
              }
            }
          }
        }
      },
      "definitions": {
        "User": {
          "type": "object",
          "properties": {
            "name": {
              "type": "string"
            }
          }
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  root_model_with_additional_properties.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum
    from typing import Optional, Union

    from pydantic import BaseModel, Field


    class NestedObjectResult(BaseModel):
        status: int


    class NestedEnumResult(Enum):
        red = 'red'
        green = 'green'


    class OneOfResult(BaseModel):
        description: Optional[str] = None


    class AnyOfResult(BaseModel):
        description: Optional[str] = None


    class User(BaseModel):
        name: Optional[str] = None


    class AllOfResult(User):
        description: Optional[str] = None


    class Model(BaseModel):
        test_id: str = Field(..., description='test ID')
        test_ip: str = Field(..., description='test IP')
        result: dict[str, int]
        nested_object_result: dict[str, NestedObjectResult]
        nested_enum_result: dict[str, NestedEnumResult]
        all_of_result: Optional[dict[str, AllOfResult]] = None
        one_of_result: Optional[dict[str, Union[User, OneOfResult]]] = None
        any_of_result: Optional[dict[str, Union[User, AnyOfResult]]] = None
        all_of_with_unknown_object: Optional[dict[str, User]] = None
        objectRef: Optional[dict[str, User]] = None
        deepNestedObjectRef: Optional[dict[str, dict[str, dict[str, User]]]] = None
    ```

    <!-- fmt: on -->

---

## `--open-enums` {#open-enums}

Accept the values an enum does not list.

APIs add enum values over time, and a closed Enum class rejects a response that carries one the client does not know
yet. `extensible` opens the enums whose schema marks them open: `x-extensible-enum: true`, or `x-ms-enum` with
`modelAsString: true`. `all` opens every enum of two or more values, except one whose schema closes it
(`x-extensible-enum: false`, `modelAsString: false`); a one-value enum is a tag, so it stays closed unless it is marked.
`none` opens no enum, as without the option, which turns the option of a preset off. A const beside an enum narrows it
to that const, so it stays closed. The list form of `x-extensible-enum`, which lists the values in place of `enum`, is
not read: such a schema has no `enum`, so its field is already a plain `str`.

An open enum is written as `Literal[...]` joined with the type of its schema, `Literal['queued', 'done'] | str`, where
`--enum-field-as-literal all` writes a `Literal`: a listed value parses as itself, as from that `Literal`, and any other
value of the type is accepted. This form wins over an option that asks for an Enum class (`--enum-field-as-literal
none`, an `enum` entry of `--enum-field-as-literal-map`, `x-enum-field-as-literal: false`). Code that read a member's
`.value` or compared with `is JobStatus.queued` compares with the string instead. With `--open-enum-base-class-map`,
an open enum written as an Enum class keeps its class on the base the map names for it.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input open_enums.yaml --formatters builtin --target-python-version 3.11 --open-enums extensible # (1)!
    ```

    1. :material-arrow-left: `--open-enums` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Jobs with open enums
      version: "1"
    paths: {}
    components:
      schemas:
        Job:
          type: object
          required: [status]
          properties:
            status:
              $ref: "#/components/schemas/JobStatus"
            provisioningState:
              $ref: "#/components/schemas/ProvisioningState"
            type:
              $ref: "#/components/schemas/JobType"
            priority:
              $ref: "#/components/schemas/Priority"
            phase:
              $ref: "#/components/schemas/Phase"
            region:
              $ref: "#/components/schemas/Region"
            tier:
              $ref: "#/components/schemas/Tier"
            tags:
              type: array
              items:
                $ref: "#/components/schemas/Tag"
            mode:
              type: string
              enum: [auto, manual]
              default: auto
              x-extensible-enum: true
            kind:
              type: string
              enum: [job]
            pinned:
              type: string
              enum: [fast, slow]
              const: fast
              x-extensible-enum: true
            quota:
              type: object
              propertyNames:
                type: string
                enum: [cpu, memory]
                x-extensible-enum: true
              additionalProperties:
                type: integer
            lifecycle:
              $ref: "#/components/schemas/Lifecycle"
            stage:
              $ref: "#/components/schemas/Stage"
            color:
              $ref: "#/components/schemas/Color"
            pet:
              $ref: "#/components/schemas/Pet"
        JobStatus:
          type: string
          enum: [queued, running, done]
          x-extensible-enum: true
        ProvisioningState:
          type: string
          enum: [Succeeded, Failed]
          x-ms-enum:
            name: ProvisioningState
            modelAsString: true
        JobType:
          type: string
          enum: [scrape, crawl]
        Priority:
          type: integer
          enum: [1, 2, 3]
          x-extensible-enum: true
        Phase:
          type: [string, "null"]
          enum: [pending, active, null]
          x-extensible-enum: true
        Region:
          type: string
          enum: [eu, us]
          x-ms-enum:
            name: Region
            modelAsString: false
        Tier:
          type: string
          enum: [free, paid]
          x-extensible-enum: false
        Tag:
          type: string
          enum: [red, blue]
          x-extensible-enum: true
        Lifecycle:
          allOf:
            - $ref: "#/components/schemas/JobStatus"
            - $ref: "#/components/schemas/ProvisioningState"
        Stage:
          allOf:
            - $ref: "#/components/schemas/JobStatus"
            - $ref: "#/components/schemas/JobType"
        Color:
          type: string
          enum: [red, blue]
        PetType:
          type: string
          enum: [cat, dog]
          x-extensible-enum: true
        Cat:
          type: object
          required: [petType]
          properties:
            petType:
              $ref: "#/components/schemas/PetType"
            meows:
              type: boolean
        Dog:
          type: object
          required: [petType]
          properties:
            petType:
              $ref: "#/components/schemas/PetType"
            barks:
              type: boolean
        Pet:
          oneOf:
            - $ref: "#/components/schemas/Cat"
            - $ref: "#/components/schemas/Dog"
          discriminator:
            propertyName: petType
            mapping:
              cat: "#/components/schemas/Cat"
              dog: "#/components/schemas/Dog"
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  open_enums.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import StrEnum
        from typing import Literal

        from pydantic import BaseModel, Field, RootModel


        class Kind(StrEnum):
            job = 'job'


        class Pinned(StrEnum):
            fast = 'fast'
            slow = 'slow'


        class JobStatus(RootModel[Literal['queued', 'running', 'done'] | str]):
            root: Literal['queued', 'running', 'done'] | str


        class ProvisioningState(RootModel[Literal['Succeeded', 'Failed'] | str]):
            root: Literal['Succeeded', 'Failed'] | str


        class JobType(StrEnum):
            scrape = 'scrape'
            crawl = 'crawl'


        class Priority(RootModel[Literal[1, 2, 3] | int]):
            root: Literal[1, 2, 3] | int


        class Phase(RootModel[Literal['pending', 'active'] | str | None]):
            root: Literal['pending', 'active'] | str | None


        class Region(StrEnum):
            eu = 'eu'
            us = 'us'


        class Tier(StrEnum):
            free = 'free'
            paid = 'paid'


        class Tag(RootModel[Literal['red', 'blue'] | str]):
            root: Literal['red', 'blue'] | str


        class Lifecycle(BaseModel):
            pass


        class Stage(StrEnum):
            scrape = 'scrape'
            crawl = 'crawl'


        class Color(StrEnum):
            red = 'red'
            blue = 'blue'


        class PetType(RootModel[Literal['cat', 'dog'] | str]):
            root: Literal['cat', 'dog'] | str


        class Cat(BaseModel):
            petType: Literal['cat']
            meows: bool | None = None


        class Dog(BaseModel):
            petType: Literal['dog']
            barks: bool | None = None


        class Pet(RootModel[Cat | Dog]):
            root: Cat | Dog = Field(..., discriminator='petType')


        class Job(BaseModel):
            status: JobStatus
            provisioningState: ProvisioningState | None = None
            type: JobType | None = None
            priority: Priority | None = None
            phase: Phase | None = None
            region: Region | None = None
            tier: Tier | None = None
            tags: list[Tag] | None = None
            mode: Literal['auto', 'manual'] | str | None = 'auto'
            kind: Kind | None = None
            pinned: Literal['fast'] | None = None
            quota: dict[Literal['cpu', 'memory'] | str, int] | None = None
            lifecycle: Lifecycle | None = None
            stage: Stage | None = None
            color: Color | None = None
            pet: Pet | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  open_enums.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum, IntEnum, StrEnum
        from typing import Literal

        from pydantic import BaseModel, Field, RootModel


        class Mode(StrEnum):
            auto = 'auto'
            manual = 'manual'


        class Kind(StrEnum):
            job = 'job'


        class Pinned(StrEnum):
            fast = 'fast'
            slow = 'slow'


        class JobStatus(StrEnum):
            queued = 'queued'
            running = 'running'
            done = 'done'


        class ProvisioningState(StrEnum):
            Succeeded = 'Succeeded'
            Failed = 'Failed'


        class JobType(StrEnum):
            scrape = 'scrape'
            crawl = 'crawl'


        class Priority(IntEnum):
            integer_1 = 1
            integer_2 = 2
            integer_3 = 3


        class Phase(Enum):
            pending = 'pending'
            active = 'active'
            NoneType_None = None


        class Region(StrEnum):
            eu = 'eu'
            us = 'us'


        class Tier(StrEnum):
            free = 'free'
            paid = 'paid'


        class Tag(StrEnum):
            red = 'red'
            blue = 'blue'


        class Lifecycle(StrEnum):
            queued = 'queued'
            running = 'running'
            done = 'done'
            Succeeded = 'Succeeded'
            Failed = 'Failed'


        class Stage(StrEnum):
            queued = 'queued'
            running = 'running'
            done = 'done'
            scrape = 'scrape'
            crawl = 'crawl'


        class Color(StrEnum):
            red = 'red'
            blue = 'blue'


        class PetType(StrEnum):
            cat = 'cat'
            dog = 'dog'


        class Cat(BaseModel):
            petType: Literal['cat']
            meows: bool | None = None


        class Dog(BaseModel):
            petType: Literal['dog']
            barks: bool | None = None


        class Pet(RootModel[Cat | Dog]):
            root: Cat | Dog = Field(..., discriminator='petType')


        class Job(BaseModel):
            status: JobStatus
            provisioningState: ProvisioningState | None = None
            type: JobType | None = None
            priority: Priority | None = None
            phase: Phase | None = None
            region: Region | None = None
            tier: Tier | None = None
            tags: list[Tag] | None = None
            mode: Mode | None = 'auto'
            kind: Kind | None = None
            pinned: Literal['fast'] | None = None
            quota: dict[Literal['cpu', 'memory'], int] | None = None
            lifecycle: Lifecycle | None = None
            stage: Stage | None = None
            color: Color | None = None
            pet: Pet | None = None
        ```

        <!-- fmt: on -->

---

## `--output-date-class` {#output-date-class}

Specify date class type for date schema fields.

The `--output-date-class` flag controls which date type to use for fields
with date format. Options include 'PastDate' for past dates only
or 'FutureDate' for future dates only. This is a Pydantic v2 only feature.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input date_class.yaml --output-date-class PastDate # (1)!
    ```

    1. :material-arrow-left: `--output-date-class` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: "3.0.0"
    components:
      schemas:
        Event:
          type: object
          required:
            - eventDate
          properties:
            eventDate:
              type: string
              format: date
              example: 2023-12-25
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  date_class.yaml
    #   timestamp: 1985-10-26T08:21:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, Field, PastDate


    class Event(BaseModel):
        eventDate: PastDate = Field(..., examples=['2023-12-25'])
    ```

    <!-- fmt: on -->

---

## `--output-datetime-class` {#output-datetime-class}

Specify datetime class type for date-time schema fields.

The `--output-datetime-class` flag controls which datetime type to use for fields
with date-time format. Options include 'AwareDatetime' for timezone-aware datetimes
or 'datetime' for standard Python datetime objects.

**See also:** [Options That Replace Post-Processing](../post-processing-options.md), [Type Mappings and Custom Types](../type-mappings.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input datetime.yaml --output-datetime-class AwareDatetime # (1)!
    ```

    1. :material-arrow-left: `--output-datetime-class` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: "3.0.0"
    components:
      schemas:
        InventoryItem:
          required:
    #      - id
    #      - name
          - releaseDate
          type: object
          properties:
    #        id:
    #          type: string
    #          format: uuid
    #          example: d290f1ee-6c54-4b01-90e6-d701748f0851
    #        name:
    #          type: string
    #          example: Widget Adapter
            releaseDate:
              type: string
              format: date-time
              example: 2016-08-29T09:12:33.001Z
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  datetime.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import AwareDatetime, BaseModel, Field


    class InventoryItem(BaseModel):
        releaseDate: AwareDatetime = Field(..., examples=['2016-08-29T09:12:33.001Z'])
    ```

    <!-- fmt: on -->

---

## `--read-allof-parents-in-mro-order` {#read-allof-parents-in-mro-order}

Read the allOf parents of every inherited schema in the order of the class bases.

A property that several bases declare takes the declaration of the first base in the method resolution order of the
generated class, the nearer one. With `BaseFirst = allOf: [A, B]` and `B = allOf: [A, {p: maxLength 3}]`, the class
is `BaseFirst(B, A)`, but the parents of the inner allOf were read in document order, so a subclass that redeclares
`p` took `A`'s `max_length=5` and accepted `"abcd"`, which `B` rejects. With `--read-allof-parents-in-mro-order` it
takes `B`'s `max_length=3`, whichever order the allOf lists `A` and `B`.

**Related:** [`--allof-merge-mode`](#allof-merge-mode)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input allof_inherited_property_nearer_base.json --read-allof-parents-in-mro-order # (1)!
    ```

    1. :material-arrow-left: `--read-allof-parents-in-mro-order` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "$defs": {
        "A": {
          "type": "object",
          "properties": {"p": {"type": "string", "maxLength": 5}, "q": {"type": "integer"}}
        },
        "B": {
          "allOf": [{"$ref": "#/$defs/A"}, {"type": "object", "properties": {"p": {"type": "string", "maxLength": 3}}}]
        },
        "BaseFirst": {"allOf": [{"$ref": "#/$defs/A"}, {"$ref": "#/$defs/B"}]},
        "DescendantFirst": {"allOf": [{"$ref": "#/$defs/B"}, {"$ref": "#/$defs/A"}]},
        "FromBaseFirst": {
          "allOf": [{"$ref": "#/$defs/BaseFirst"}, {"type": "object", "properties": {"p": {"minLength": 1}}, "required": ["p", "q"]}]
        },
        "FromDescendantFirst": {
          "allOf": [
            {"$ref": "#/$defs/DescendantFirst"},
            {"type": "object", "properties": {"p": {"minLength": 1}}, "required": ["p", "q"]}
          ]
        }
      },
      "type": "object",
      "properties": {
        "base_first": {"$ref": "#/$defs/FromBaseFirst"},
        "descendant_first": {"$ref": "#/$defs/FromDescendantFirst"}
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  allof_inherited_property_nearer_base.json

        from __future__ import annotations

        from pydantic import BaseModel, constr


        class A(BaseModel):
            p: constr(max_length=5) | None = None
            q: int | None = None


        class B(A):
            p: constr(max_length=3) | None = None


        class BaseFirst(B, A):
            pass


        class DescendantFirst(B, A):
            pass


        class FromBaseFirst(BaseFirst):
            p: constr(min_length=1, max_length=3)
            q: int


        class FromDescendantFirst(DescendantFirst):
            p: constr(min_length=1, max_length=3)
            q: int


        class Model(BaseModel):
            base_first: FromBaseFirst | None = None
            descendant_first: FromDescendantFirst | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  allof_inherited_property_nearer_base.json

        from __future__ import annotations

        from pydantic import BaseModel, constr


        class A(BaseModel):
            p: constr(max_length=5) | None = None
            q: int | None = None


        class B(A):
            p: constr(max_length=3) | None = None


        class BaseFirst(B, A):
            pass


        class DescendantFirst(B, A):
            pass


        class FromBaseFirst(BaseFirst):
            p: constr(min_length=1, max_length=5)
            q: int


        class FromDescendantFirst(DescendantFirst):
            p: constr(min_length=1, max_length=3)
            q: int


        class Model(BaseModel):
            base_first: FromBaseFirst | None = None
            descendant_first: FromDescendantFirst | None = None
        ```

        <!-- fmt: on -->

---

## `--read-const-beside-enum` {#read-const-beside-enum}

Read a `const` beside an `enum` as JSON Schema does.

A `const` that admits some but not all values of the `enum` beside it narrows the schema to the admitted
values, while the enum class keeps every value as written. With `--read-const-beside-enum`, a value whose schema
names such an enum, a property, an item, a map value or key, a union branch or a `$ref` to a definition, is typed
by the const: `Literal[<Enum>.<member>]` when the members of the enum equal their values (`IntEnum`, `StrEnum`,
`--use-subclass-enum`), the const's own `Literal`, or `None` for a null const. A plain `Enum`, and any msgspec
enum, keeps the enum type and warns once per schema location that the other values are accepted. A schema whose
const is none of its enum values admits no value and is generated as written with a warning. Under
`--use-single-value-enum-as-const`, an `enum` that admits the `const` beside it, such as
`{"enum": ["a", "b"], "const": "a"}`, is read as that `const` without the enum class written beside it otherwise,
except one with `nullable: true` and a const other than `null`. Without the option, a `const` beside an `enum` is
ignored.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input enum_const_positions.json --read-const-beside-enum # (1)!
    ```

    1. :material-arrow-left: `--read-const-beside-enum` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "Positions",
      "type": "object",
      "properties": {
        "property": {"type": "string", "enum": ["a", "b"], "const": "a"},
        "propertySingle": {"type": "string", "enum": ["a"], "const": "a"},
        "words": {"type": "array", "items": {"type": "string", "enum": ["a", "b"], "const": "b"}},
        "counts": {"type": "object", "additionalProperties": {"type": "integer", "enum": [1, 2, 3], "const": 2}},
        "branch": {"anyOf": [{"type": "string", "enum": ["a", "b"], "const": "a"}, {"type": "integer"}]},
        "refSibling": {"$ref": "#/$defs/Switch", "const": "on"},
        "keys": {"type": "object", "propertyNames": {"enum": ["a", "b"], "const": "a"}, "additionalProperties": {"type": "integer"}},
        "refKeys": {"type": "object", "propertyNames": {"$ref": "#/$defs/Narrowed"}, "additionalProperties": {"type": "integer"}},
        "definition": {"$ref": "#/$defs/Narrowed"},
        "definitionSingle": {"$ref": "#/$defs/Single"},
        "allOfDefinition": {"$ref": "#/$defs/AllOfNarrowed"},
        "besideNull": {"$ref": "#/$defs/BesideNull"},
        "constNull": {"$ref": "#/$defs/ConstNull"}
      },
      "$defs": {
        "Switch": {"type": "string", "enum": ["on", "off"]},
        "Narrowed": {
          "type": "string",
          "enum": ["a", "b", "c"],
          "const": "b",
          "x-enum-varnames": ["ALPHA", "BRAVO", "CHARLIE"],
          "x-enum-descriptions": ["First.", "Second.", "Third."]
        },
        "Single": {"type": "string", "enum": ["a"], "const": "a"},
        "AllOfNarrowed": {"allOf": [{"type": "string", "enum": ["a", "b"], "const": "b"}]},
        "BesideNull": {"type": "string", "enum": ["a", null, "b"], "const": "b", "x-enum-varnames": ["ALPHA", "BRAVO"]},
        "ConstNull": {"type": "string", "enum": ["a", null, "b"], "const": null}
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  enum_const_positions.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum, IntEnum
    from typing import Literal

    from pydantic import BaseModel, Field, RootModel


    class Property(Enum):
        a = 'a'
        b = 'b'


    class PropertySingle(Enum):
        a = 'a'


    class Word(Enum):
        a = 'a'
        b = 'b'


    class Counts(IntEnum):
        integer_1 = 1
        integer_2 = 2
        integer_3 = 3


    class Branch(Enum):
        a = 'a'
        b = 'b'


    class RefSibling(Enum):
        on = 'on'
        off = 'off'


    class Switch(Enum):
        on = 'on'
        off = 'off'


    class Narrowed(Enum):
        ALPHA = 'a'
        BRAVO = 'b'
        CHARLIE = 'c'


    class Single(Enum):
        a = 'a'


    class AllOfNarrowed(Enum):
        a = 'a'
        b = 'b'


    class BesideNullEnum(Enum):
        ALPHA = 'a'
        BRAVO = 'b'


    class BesideNull(RootModel[Literal['b'] | None]):
        root: Literal['b'] | None = None


    class ConstNullEnum(Enum):
        a = 'a'
        b = 'b'


    class ConstNull(RootModel[None]):
        root: None = Field(None)


    class Positions(BaseModel):
        property: Literal['a'] | None = None
        propertySingle: Literal['a'] | None = None
        words: list[Word] | None = None
        counts: dict[str, Literal[Counts.integer_2]] | None = None
        branch: Branch | int | None = None
        refSibling: Literal['on'] | None = None
        keys: dict[Literal['a'], int] | None = None
        refKeys: dict[Narrowed, int] | None = None
        definition: Narrowed | None = None
        definitionSingle: Single | None = None
        allOfDefinition: AllOfNarrowed | None = None
        besideNull: BesideNull | None = None
        constNull: ConstNull | None = None
    ```

    <!-- fmt: on -->

---

## `--read-format-as-annotation` {#read-format-as-annotation}

Read the format of a schema as an annotation, as JSON Schema does.

JSON Schema reads `format` as an annotation: a validator does not reject a string whose format does not match, and
the value stays the string. Without this option each known format selects a type, as in 0.96.0: `email` is
`EmailStr` (which needs `email-validator` to import, and drops `maxLength`), `date-time` is `AwareDatetime` (which
rejects a date-time without an offset), `password` is `SecretStr` (which dumps `**********`), `uri` is `AnyUrl`
(which dumps `https://example.com/` for `https://example.com`), and `uuid`, `date`, `ipv4` and `decimal` are parsed
into Python objects. With it a string, integer or number schema takes the type its `type` keyword gives, `str`, `int`
or `float`, with its own `minLength`, `maxLength`, `pattern` and bounds, so the models accept every value the schema
accepts and dump a string or an integer as written (a number as the `float` it parses to, as without a format), and an
unknown format gives no warning. Two formats still select a type:
`format: binary`, which stays `bytes` (raw octets, not a JSON string), and a format that a `--type-mappings` entry
names for a type the schema declares (`--type-mappings date-time=date-time` keeps `date-time` typed, and then
`--output-datetime-class` applies). The format is not written into the models. The option reads JSON Schema, OpenAPI,
the JSON Schema payloads of AsyncAPI and MCP tool schemas, with every document they reference; inputs converted from
Avro, Protocol Buffers or XML Schema keep the types of their formats.

**See also:** [Type Mappings and Custom Types](../type-mappings.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input tool_input_formats.json --read-format-as-annotation # (1)!
    ```

    1. :material-arrow-left: `--read-format-as-annotation` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "ScheduleCall",
      "description": "Input of a tool that schedules a call with a contact.",
      "type": "object",
      "properties": {
        "contactEmail": {"type": "string", "format": "email", "maxLength": 64},
        "profileUrl": {"type": "string", "format": "uri"},
        "startsAt": {"type": "string", "format": "date-time"},
        "callDate": {"type": "string", "format": "date"},
        "dialPin": {"type": "string", "format": "password"},
        "attachment": {"type": "string", "format": "byte"},
        "recording": {"type": "string", "format": "binary"},
        "callId": {"type": "string", "format": "uuid"},
        "bridgeHost": {"type": "string", "format": "hostname", "minLength": 3},
        "clientIp": {"type": "string", "format": "ipv4"},
        "fee": {"type": "number", "format": "decimal", "minimum": 0},
        "feeText": {"type": "string", "format": "decimal"},
        "createdEpoch": {"type": "integer", "format": "date-time"},
        "attempts": {"type": "integer", "format": "int32", "minimum": 1},
        "phone": {"type": "string", "format": "x-phone", "pattern": "^\\+[0-9]+$"},
        "volume": {"type": "integer", "format": "uint8"},
        "slot": {"type": "string", "format": "date", "enum": ["2026-11-20", "2026-11-21"]},
        "backupEmail": {"allOf": [{"$ref": "#/$defs/Mailbox"}, {"format": "email", "maxLength": 40}]},
        "notes": {"type": ["string", "null"], "format": "uri-reference"},
        "rescheduledAt": {"type": ["string", "null"], "format": "date-time"},
        "dialIn": {"type": "string", "format": "uri", "enum": ["https://voice.example.com/join"]},
        "format": {"type": "string", "format": "date-time"},
        "settings": {
          "type": "object",
          "properties": {"timezone": {"type": "string"}},
          "examples": [{"format": "24h", "timezone": "UTC"}]
        }
      },
      "required": ["contactEmail", "startsAt", "callId"],
      "$defs": {
        "Mailbox": {"type": "string", "format": "email"}
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  tool_input_formats.json

        from __future__ import annotations

        from enum import Enum

        from pydantic import BaseModel, Field, RootModel, confloat, conint, constr


        class Slot(Enum):
            field_2026_11_20 = '2026-11-20'
            field_2026_11_21 = '2026-11-21'


        class DialIn(Enum):
            https___voice_example_com_join = 'https://voice.example.com/join'


        class Settings(BaseModel):
            timezone: str | None = None


        class Mailbox(RootModel[str]):
            root: str


        class ScheduleCall(BaseModel):
            contactEmail: constr(max_length=64)
            profileUrl: str | None = None
            startsAt: str
            callDate: str | None = None
            dialPin: str | None = None
            attachment: str | None = None
            recording: bytes | None = None
            callId: str
            bridgeHost: constr(min_length=3) | None = None
            clientIp: str | None = None
            fee: confloat(ge=0.0) | None = None
            feeText: str | None = None
            createdEpoch: int | None = None
            attempts: conint(ge=1) | None = None
            phone: constr(pattern=r'^\+[0-9]+$') | None = None
            volume: int | None = None
            slot: Slot | None = None
            backupEmail: Mailbox | None = None
            notes: str | None = None
            rescheduledAt: str | None = None
            dialIn: DialIn | None = None
            format: str | None = None
            settings: Settings | None = Field(
                None, examples=[{'format': '24h', 'timezone': 'UTC'}]
            )
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  tool_input_formats.json

        from __future__ import annotations

        from datetime import date
        from decimal import Decimal
        from enum import Enum
        from ipaddress import IPv4Address
        from uuid import UUID

        from pydantic import (
            AnyUrl,
            AwareDatetime,
            Base64Str,
            BaseModel,
            EmailStr,
            Field,
            RootModel,
            SecretStr,
            condecimal,
            conint,
            constr,
        )


        class Slot(Enum):
            field_2026_11_20 = '2026-11-20'
            field_2026_11_21 = '2026-11-21'


        class DialIn(Enum):
            https___voice_example_com_join = 'https://voice.example.com/join'


        class Settings(BaseModel):
            timezone: str | None = None


        class Mailbox(RootModel[EmailStr]):
            root: EmailStr


        class ScheduleCall(BaseModel):
            contactEmail: EmailStr
            profileUrl: AnyUrl | None = None
            startsAt: AwareDatetime
            callDate: date | None = None
            dialPin: SecretStr | None = None
            attachment: Base64Str | None = None
            recording: bytes | None = None
            callId: UUID
            bridgeHost: (
                constr(
                    pattern=r'^(([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])\.)*([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9\-]{0,61}[A-Za-z0-9])$'
                )
                | None
            ) = None
            clientIp: IPv4Address | None = None
            fee: condecimal(ge=Decimal('0')) | None = None
            feeText: Decimal | None = None
            createdEpoch: AwareDatetime | None = None
            attempts: conint(ge=1) | None = None
            phone: constr(pattern=r'^\+[0-9]+$') | None = None
            volume: int | None = None
            slot: Slot | None = None
            backupEmail: Mailbox | None = None
            notes: str | None = None
            rescheduledAt: AwareDatetime | None = None
            dialIn: DialIn | None = None
            format: AwareDatetime | None = None
            settings: Settings | None = Field(
                None, examples=[{'format': '24h', 'timezone': 'UTC'}]
            )
        ```

        <!-- fmt: on -->

---

## `--read-kubernetes-extensions` {#read-kubernetes-extensions}

Read the Kubernetes schema extensions as the keywords they stand for.

Kubernetes schemas, the `openAPIV3Schema` of a CustomResourceDefinition and the `/openapi/v3` documents a cluster
serves, carry extensions that change what a value may be. Without this option they are ignored. With it, wherever a
schema object writes them:

- `x-kubernetes-int-or-string: true`, or the format `int-or-string`, is read as `type: [integer, string]` in place of an
  `anyOf` or `oneOf` of exactly those two types, so a bare one is `int | str` instead of `Any`, and a quantity (that
  `anyOf` beside a `pattern`) is one `int | constr(pattern=...)` instead of two root models;
- `x-kubernetes-preserve-unknown-fields: true` beside `properties` and no `additionalProperties` is read as
  `additionalProperties: true`, so a Pydantic model keeps the keys it does not declare (`extra='allow'`);
- `x-kubernetes-group-version-kind` gives the `apiVersion` and the `kind` property, each where all its entries name one
  value and the property is a plain string, that value as `const` and `default`, so they are `Literal['apps/v1'] =
  'apps/v1'` and `Literal['Deployment'] = 'Deployment'`. Where `const` is no keyword (draft-04) it is not read.

The other extensions (`x-kubernetes-list-type`, `x-kubernetes-list-map-keys`, `x-kubernetes-map-type`,
`x-kubernetes-validations`, `x-kubernetes-embedded-resource`) stay as they are; `--field-extra-keys` carries them into
the field.

**See also:** [Kubernetes Schemas](../kubernetes.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input kubernetes_crd_schema.yaml --read-kubernetes-extensions # (1)!
    ```

    1. :material-arrow-left: `--read-kubernetes-extensions` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    $schema: http://json-schema.org/draft-07/schema#
    title: Widget
    description: Widget is the Schema for the widgets API.
    type: object
    x-kubernetes-group-version-kind:
    - group: example.com
      kind: Widget
      version: v1
    required:
    - spec
    properties:
      apiVersion:
        description: APIVersion defines the versioned schema of this representation of an object.
        type: string
      kind:
        description: Kind is a string value representing the REST resource this object represents.
        type: string
      metadata:
        type: object
      spec:
        description: WidgetSpec defines the desired state of Widget.
        type: object
        required:
        - replicas
        properties:
          replicas:
            type: integer
            format: int32
          maxSurge:
            anyOf:
            - type: integer
            - type: string
            x-kubernetes-int-or-string: true
          port:
            x-kubernetes-int-or-string: true
          targetPort:
            type:
            - integer
            - string
            x-kubernetes-int-or-string: true
          resources:
            type: object
            properties:
              limits:
                type: object
                additionalProperties:
                  anyOf:
                  - type: integer
                  - type: string
                  pattern: ^(\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))(([KMGTPE]i)|[numkMGTPE]|([eE](\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))))?$
                  x-kubernetes-int-or-string: true
          selector:
            type: object
            properties:
              app:
                type: string
            x-kubernetes-preserve-unknown-fields: true
          config:
            x-kubernetes-preserve-unknown-fields: true
          template:
            type: object
            x-kubernetes-embedded-resource: true
            x-kubernetes-preserve-unknown-fields: true
          ports:
            type: array
            items:
              type: object
              required:
              - name
              properties:
                name:
                  type: string
                port:
                  type: integer
            x-kubernetes-list-map-keys:
            - name
            x-kubernetes-list-type: map
          finalizers:
            type: array
            items:
              type: string
            x-kubernetes-list-type: set
          labels:
            type: object
            additionalProperties:
              type: string
            x-kubernetes-map-type: atomic
          paused:
            type: boolean
            nullable: true
          updatePolicy:
            type: string
            enum:
            - Recreate
        x-kubernetes-validations:
        - message: replicas must not be negative
          rule: self.replicas >= 0
      status:
        type: object
        properties:
          readyReplicas:
            type: integer
          conditions:
            type: array
            items:
              type: object
              required:
              - type
              - status
              properties:
                type:
                  type: string
                status:
                  type: string
                  enum:
                  - "True"
                  - "False"
                  - Unknown
            x-kubernetes-list-map-keys:
            - type
            x-kubernetes-list-type: map
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  kubernetes_crd_schema.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum
        from typing import Any, Literal

        from pydantic import BaseModel, ConfigDict, Field, constr


        class Resources(BaseModel):
            limits: (
                dict[
                    str,
                    int
                    | constr(
                        pattern=r'^(\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))(([KMGTPE]i)|[numkMGTPE]|([eE](\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))))?$'
                    ),
                ]
                | None
            ) = None


        class Selector(BaseModel):
            model_config = ConfigDict(
                extra='allow',
            )
            app: str | None = None


        class Port(BaseModel):
            name: str
            port: int | None = None


        class UpdatePolicy(Enum):
            Recreate = 'Recreate'


        class Spec(BaseModel):
            replicas: int
            maxSurge: int | str | None = None
            port: int | str | None = None
            targetPort: int | str | None = None
            resources: Resources | None = None
            selector: Selector | None = None
            config: Any | None = None
            template: dict[str, Any] | None = None
            ports: list[Port] | None = None
            finalizers: list[str] | None = None
            labels: dict[str, str] | None = None
            paused: bool | None = None
            updatePolicy: UpdatePolicy | None = None


        class Status1(Enum):
            True_ = 'True'
            False_ = 'False'
            Unknown = 'Unknown'


        class Condition(BaseModel):
            type: str
            status: Status1


        class Status(BaseModel):
            readyReplicas: int | None = None
            conditions: list[Condition] | None = None


        class Widget(BaseModel):
            apiVersion: Literal['example.com/v1'] = Field(
                'example.com/v1',
                description='APIVersion defines the versioned schema of this representation of an object.',
            )
            kind: Literal['Widget'] = Field(
                'Widget',
                description='Kind is a string value representing the REST resource this object represents.',
            )
            metadata: dict[str, Any] | None = None
            spec: Spec = Field(
                ..., description='WidgetSpec defines the desired state of Widget.'
            )
            status: Status | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  kubernetes_crd_schema.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum
        from typing import Any

        from pydantic import BaseModel, Field, RootModel, constr


        class Limits(RootModel[int]):
            root: int


        class Limits1(
            RootModel[
                constr(
                    pattern=r'^(\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))(([KMGTPE]i)|[numkMGTPE]|([eE](\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))))?$'
                )
            ]
        ):
            root: constr(
                pattern=r'^(\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))(([KMGTPE]i)|[numkMGTPE]|([eE](\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))))?$'
            )


        class Resources(BaseModel):
            limits: dict[str, Limits | Limits1] | None = None


        class Selector(BaseModel):
            app: str | None = None


        class Port(BaseModel):
            name: str
            port: int | None = None


        class UpdatePolicy(Enum):
            Recreate = 'Recreate'


        class Spec(BaseModel):
            replicas: int
            maxSurge: int | str | None = None
            port: Any | None = None
            targetPort: int | str | None = None
            resources: Resources | None = None
            selector: Selector | None = None
            config: Any | None = None
            template: dict[str, Any] | None = None
            ports: list[Port] | None = None
            finalizers: list[str] | None = None
            labels: dict[str, str] | None = None
            paused: bool | None = None
            updatePolicy: UpdatePolicy | None = None


        class Status1(Enum):
            True_ = 'True'
            False_ = 'False'
            Unknown = 'Unknown'


        class Condition(BaseModel):
            type: str
            status: Status1


        class Status(BaseModel):
            readyReplicas: int | None = None
            conditions: list[Condition] | None = None


        class Widget(BaseModel):
            apiVersion: str | None = Field(
                None,
                description='APIVersion defines the versioned schema of this representation of an object.',
            )
            kind: str | None = Field(
                None,
                description='Kind is a string value representing the REST resource this object represents.',
            )
            metadata: dict[str, Any] | None = None
            spec: Spec = Field(
                ..., description='WidgetSpec defines the desired state of Widget.'
            )
            status: Status | None = None
        ```

        <!-- fmt: on -->

---

## `--read-unevaluated-properties` {#read-unevaluated-properties}

Read a schema-valued `unevaluatedProperties` as `additionalProperties`.

Where no applicator (`allOf`, `anyOf`, `oneOf`, `not`, `if`, `dependentSchemas`, `dependencies` or a reference)
evaluates a property of the object in place, the keys `unevaluatedProperties` evaluates are exactly those that
`additionalProperties` would, so its schema types the extra keys of a model (`__pydantic_extra__: Dict[str, str]`)
and makes an object without properties a map (`RootModel[dict[str, int]]`). The schema object as written decides:
the models built from its branches keep the extra keys untyped where its applicators evaluate properties. Without
the option the extra keys are allowed untyped, as in 0.86.0.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input unevaluated_properties_schema.json --read-unevaluated-properties # (1)!
    ```

    1. :material-arrow-left: `--read-unevaluated-properties` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
        "$id": "https://example.com/test.schema.json",
        "title": "Resource",
        "type": "object",
        "properties": {
            "name": { "type": "string" }
        },
        "required": ["name"],
        "unevaluatedProperties": {
            "type": "string"
        }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  unevaluated_properties_schema.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Dict

        from pydantic import BaseModel, ConfigDict


        class Resource(BaseModel):
            model_config = ConfigDict(
                extra='allow',
            )
            __annotations__ = {
                '__pydantic_extra__': Dict[str, str],
            }
            name: str
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  unevaluated_properties_schema.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, ConfigDict


        class Resource(BaseModel):
            model_config = ConfigDict(
                extra='allow',
            )
            name: str
        ```

        <!-- fmt: on -->

---

## `--strict-types` {#strict-types}

Enable strict type validation for specified Python types.

The --strict-types option enforces stricter type checking by preventing implicit
type coercion for the specified types (str, bytes, int, float, bool). This
generates StrictStr, StrictBytes, StrictInt, StrictFloat, and StrictBool types
in Pydantic models, ensuring values match exactly without automatic conversion.

**See also:** [Type Mappings and Custom Types](../type-mappings.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input strict_types.json --strict-types str bytes int float bool # (1)!
    ```

    1. :material-arrow-left: `--strict-types` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "User",
      "type": "object",
      "properties": {
            "name": {
              "type": "string",
              "example": "ken"
            },
            "age": {
              "type": "integer"
            },
            "salary": {
              "type": "integer",
              "minimum": 0
            },
            "debt" : {
              "type": "integer",
              "maximum": 0
            },
            "loan" : {
              "type": "number",
              "maximum": 0
            },
            "tel": {
              "type": "string",
              "pattern": "^(\\([0-9]{3}\\))?[0-9]{3}-[0-9]{4}$"
            },
            "height": {
              "type": "number",
              "minimum": 0
            },
            "weight": {
              "type": "number",
              "minimum": 0
            },
            "score": {
              "type": "number",
              "minimum": 1e-08
            },
            "active": {
              "type": "boolean"
            },
            "photo": {
              "type": "string",
              "format": "binary",
              "minLength": 100
            }
          }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  strict_types.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import (
        BaseModel,
        Field,
        StrictBool,
        StrictInt,
        StrictStr,
        conbytes,
        confloat,
        conint,
        constr,
    )


    class User(BaseModel):
        name: StrictStr | None = Field(None, examples=['ken'])
        age: StrictInt | None = None
        salary: conint(ge=0, strict=True) | None = None
        debt: conint(le=0, strict=True) | None = None
        loan: confloat(le=0.0, strict=True) | None = None
        tel: constr(
            pattern=r'^(\([0-9]{3}\))?[0-9]{3}-[0-9]{4}$', strict=True
        ) | None = None
        height: confloat(ge=0.0, strict=True) | None = None
        weight: confloat(ge=0.0, strict=True) | None = None
        score: confloat(ge=1e-08, strict=True) | None = None
        active: StrictBool | None = None
        photo: conbytes(min_length=100, strict=True) | None = None
    ```

    <!-- fmt: on -->

---

## `--type-mappings` {#type-mappings}

Override default type mappings for schema formats.

The `--type-mappings` flag configures the code generation behavior.

**See also:** [Kubernetes Schemas](../kubernetes.md), [Options That Replace Post-Processing](../post-processing-options.md), [Type Mappings and Custom Types](../type-mappings.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input type_mappings.json --output-model-type pydantic_v2.BaseModel --type-mappings binary=string # (1)!
    ```

    1. :material-arrow-left: `--type-mappings` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "BlobModel",
      "type": "object",
      "properties": {
        "content": {
          "type": "string",
          "format": "binary",
          "description": "Binary content that should be mapped to string"
        },
        "data": {
          "type": "string",
          "format": "byte",
          "description": "Base64 encoded data"
        },
        "name": {
          "type": "string",
          "description": "Regular string field"
        }
      },
      "required": ["content", "data", "name"]
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  type_mappings.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import Base64Str, BaseModel, Field


    class BlobModel(BaseModel):
        content: str = Field(
            ..., description='Binary content that should be mapped to string'
        )
        data: Base64Str = Field(..., description='Base64 encoded data')
        name: str = Field(..., description='Regular string field')
    ```

    <!-- fmt: on -->

---

## `--type-overrides` {#type-overrides}

Replace schema model types with custom Python types via JSON mapping.

This option is useful for importing models from external libraries (like `geojson-pydantic`)
instead of generating them.

**Override Formats:**

| Format | Description |
|--------|-------------|
| `{"ModelName": "package.Type"}` | Model-level: Skip generation; replace field and inheritance refs |
| `{"Model.field": "package.Type"}` | Scoped: Override only specific field in specific model |

**Common Use Cases:**

| Use Case | Example Override |
|----------|------------------|
| GeoJSON types | `{"Feature": "geojson_pydantic.Feature"}` |
| Custom datetime | `{"Timestamp": "pendulum.DateTime"}` |
| MongoDB ObjectId | `{"ObjectId": "bson.ObjectId"}` |
| Custom validators | `{"Email": "my_app.types.ValidatedEmail"}` |

**See also:** [Kubernetes Schemas](../kubernetes.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input type_overrides_test.json --type-overrides '{"CustomType": "my_app.types.CustomType"}' # (1)!
    ```

    1. :material-arrow-left: `--type-overrides` - the option documented here

!!! note "Model-level overrides skip generation"
    When you specify a model-level override (without a dot in the key), the generator will
    **skip generating that model entirely** and import it from the specified package instead.
    References to that model are replaced in field annotations and `allOf` inheritance base classes.


??? example "Examples"

    **Input Schema:**

    ```json
    {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {
            "CustomType": {"type": "string"},
            "User": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "custom": {"$ref": "#/definitions/CustomType"}
                }
            }
        }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  type_overrides_test.json
    #   timestamp: 1985-10-26T08:21:00+00:00

    from __future__ import annotations

    from typing import Any

    from my_app.types import CustomType
    from pydantic import BaseModel, RootModel


    class Model(RootModel[Any]):
        root: Any


    class User(BaseModel):
        id: int | None = None
        custom: CustomType | None = None
    ```

    <!-- fmt: on -->

---

## `--type-overrides-replace-whole-type` {#type-overrides-replace-whole-type}

Make every `--type-overrides` value of a field replace the field's whole type.

A class path of `--type-overrides` names the type of the whole field, but without this option the field keeps the dict
of an object map around it: `{"M.f": "builtins.dict"}` on a map of integers writes `dict[str, dict]`, which rejects
`{"a": 1}`, valid for the schema, and `decimal.Decimal` writes a dict of `Decimal` values. With it the class replaces
the map itself (`dict`, `Decimal`), as an annotation such as `dict[str, int]` already does, and a `propertyNames` key
type leaves with the map.

The item counts of an array and the property counts of a map that an override replaces stay only where the new type
counts the same: a sequence class its items (`builtins.tuple`), a mapping class its keys (`builtins.dict`,
`collections.abc.Mapping`), or a type the generator cannot classify. Without the option they stay whatever the new type
is: a `str` in place of an array with `maxItems` limits the length of the string (`notes`), and `typing.Any` in place of
an array with `minItems` checks the length of every value and raises `TypeError` for one without a length. A count the
new type does not count is left out with a warning.

**See also:** [Kubernetes Schemas](../kubernetes.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input type_overrides_replace_whole_type.json --type-overrides '{"Inventory.counts": "builtins.dict", "Inventory.sized": "builtins.dict", "Inventory.amounts": "typing.Any", "Inventory.keyed": "collections.abc.Mapping", "Inventory.labels": "dict[str, str]", "Inventory.tags": "builtins.tuple", "Inventory.notes": "builtins.str", "Inventory.code": "builtins.bytes"}' --type-overrides-replace-whole-type # (1)!
    ```

    1. :material-arrow-left: `--type-overrides-replace-whole-type` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "Inventory",
      "type": "object",
      "properties": {
        "counts": {"type": "object", "additionalProperties": {"type": "integer"}},
        "sized": {"type": "object", "additionalProperties": {"type": "integer"}, "minProperties": 1},
        "amounts": {"type": "object", "additionalProperties": {"type": "number"}, "maxProperties": 2},
        "keyed": {"type": "object", "additionalProperties": {"type": "string"}, "propertyNames": {"maxLength": 2}},
        "labels": {"type": "object", "additionalProperties": {"type": "string"}, "minProperties": 1},
        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "notes": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "code": {"type": "string", "minLength": 2}
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  type_overrides_replace_whole_type.json

        from __future__ import annotations

        from builtins import bytes, dict, str, tuple
        from collections.abc import Mapping
        from typing import Any

        from pydantic import BaseModel, Field


        class Inventory(BaseModel):
            counts: dict | None = None
            sized: dict | None = Field(None, min_length=1)
            amounts: Any | None = None
            keyed: Mapping | None = None
            labels: dict[str, str] | None = Field(None, min_length=1)
            tags: tuple | None = Field(None, min_length=1)
            notes: str | None = None
            code: bytes | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  type_overrides_replace_whole_type.json

        from __future__ import annotations

        from builtins import bytes, dict, str, tuple
        from collections.abc import Mapping
        from typing import Any

        from pydantic import BaseModel, Field, constr


        class Inventory(BaseModel):
            counts: dict[str, dict] | None = None
            sized: dict[str, dict] | None = Field(None, min_length=1)
            amounts: dict[str, Any] | None = Field(None, max_length=2)
            keyed: dict[constr(max_length=2), Mapping] | None = None
            labels: dict[str, str] | None = Field(None, min_length=1)
            tags: tuple | None = Field(None, min_length=1)
            notes: str | None = Field(None, max_length=3)
            code: bytes | None = None
        ```

        <!-- fmt: on -->

---

## `--untyped-value-type` {#untyped-value-type}

Write each value the schema leaves untyped as one type instead of `Any`.

A value the schema leaves untyped, `{}`, `true`, a schema whose keywords give it no type (`{"description": "..."}`, not
an `enum`), the values of an object without properties and the items of an array without items, is written as `Any`, and
under `--use-object-type` the values and items of containers as `object`, as in 0.96.0. With `--untyped-value-type
pydantic.JsonValue` each of them is written as Pydantic's `JsonValue` (Pydantic 2.5 or later), in every position: a
field, a union member, a dict value or key's value, a list item, the root of a root model, the value of a type alias and
`__pydantic_extra__`. The structure is as without the option, and the models parse a JSON document to the same values;
Python values that are no JSON values, such as a tuple, a set or a `datetime`, are rejected. The value may also be
`object`, `Any`, or the dotted import path of a type of your own, which the models import (for example a recursive alias
for `typing.TypedDict` output). The option takes precedence over `--use-object-type`; `--type-overrides`,
`x-python-type` and `x-python-import` keep their types, and `msgspec.Struct` output keeps `Any`, which msgspec decodes
into JSON values. A definition named like the imported type is renamed by the rule for names an import takes
(`JsonValue` becomes `JsonValueModel`), unless `--collapse-root-models` writes the type at its references.

**See also:** [Type Mappings and Custom Types](../type-mappings.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input event_log_untyped.json --untyped-value-type pydantic.JsonValue # (1)!
    ```

    1. :material-arrow-left: `--untyped-value-type` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "AuditEvent",
      "description": "One entry of an audit log, whose payload and context the producer leaves free.",
      "type": "object",
      "properties": {
        "payload": {},
        "previous": true,
        "note": {"description": "Free-form value the producer attaches"},
        "context": {"type": "object"},
        "flags": {"type": "object", "additionalProperties": true},
        "attributes": {"type": "object", "additionalProperties": {}},
        "tags": {"type": "array"},
        "samples": {"type": "array", "items": {}},
        "extra": {"type": ["object", "null"]},
        "labelOrObject": {"type": ["string", "object"]},
        "choice": {"anyOf": [{"type": "string"}, {}]},
        "notText": {"not": {"type": "string"}},
        "headers": {"type": "object", "patternProperties": {"^x-": {}}},
        "snapshot": {"$ref": "#/$defs/JsonValue"},
        "baseline": {"$ref": "#/$defs/JsonValue"},
        "actor": {
          "type": "object",
          "properties": {"id": {"type": "integer"}},
          "additionalProperties": {}
        },
        "rawJson": {"type": "string", "x-python-type": "Any"}
      },
      "required": ["payload"],
      "$defs": {"JsonValue": {}}
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  event_log_untyped.json

        from __future__ import annotations

        from typing import Any, Dict

        from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, constr


        class Actor(BaseModel):
            model_config = ConfigDict(
                extra='allow',
            )
            __annotations__ = {
                '__pydantic_extra__': Dict[str, JsonValue],
            }
            id: int | None = None


        class JsonValueModel(RootModel[JsonValue]):
            root: JsonValue


        class AuditEvent(BaseModel):
            payload: JsonValue
            previous: JsonValue | None = None
            note: JsonValue | None = Field(
                None, description='Free-form value the producer attaches'
            )
            context: dict[str, JsonValue] | None = None
            flags: dict[str, JsonValue] | None = None
            attributes: dict[str, JsonValue] | None = None
            tags: list[JsonValue] | None = None
            samples: list[JsonValue] | None = None
            extra: dict[str, JsonValue] | None = None
            labelOrObject: str | dict[str, JsonValue] | None = None
            choice: str | JsonValue | None = None
            notText: JsonValue | None = None
            headers: dict[constr(pattern=r'^x-'), JsonValue] | None = None
            snapshot: JsonValueModel | None = None
            baseline: JsonValueModel | None = None
            actor: Actor | None = None
            rawJson: Any | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  event_log_untyped.json

        from __future__ import annotations

        from typing import Any, Dict

        from pydantic import BaseModel, ConfigDict, Field, RootModel, constr


        class Actor(BaseModel):
            model_config = ConfigDict(
                extra='allow',
            )
            __annotations__ = {
                '__pydantic_extra__': Dict[str, Any],
            }
            id: int | None = None


        class JsonValue(RootModel[Any]):
            root: Any


        class AuditEvent(BaseModel):
            payload: Any
            previous: Any | None = None
            note: Any | None = Field(None, description='Free-form value the producer attaches')
            context: dict[str, Any] | None = None
            flags: dict[str, Any] | None = None
            attributes: dict[str, Any] | None = None
            tags: list[Any] | None = None
            samples: list[Any] | None = None
            extra: dict[str, Any] | None = None
            labelOrObject: str | dict[str, Any] | None = None
            choice: str | Any | None = None
            notText: Any | None = None
            headers: dict[constr(pattern=r'^x-'), Any] | None = None
            snapshot: JsonValue | None = None
            baseline: JsonValue | None = None
            actor: Actor | None = None
            rawJson: Any | None = None
        ```

        <!-- fmt: on -->

---

## `--use-annotated` {#use-annotated}

Use typing.Annotated for Field() with constraints.

The `--use-annotated` flag generates Field definitions using typing.Annotated
syntax instead of default values. This also enables `--field-constraints`.

`--use-annotated` alone does not preserve `uniqueItems: true` as set semantics.
Use [`--use-unique-items-as-set`](#use-unique-items-as-set) when you need the
generated type to enforce uniqueness for array schemas.

**Related:** [`--field-constraints`](field-customization.md#field-constraints)

**See also:** [Python Version Compatibility](../python-version-compatibility.md)

**Option relationships:**

- **Implies:** [`--field-constraints`](field-customization.md#field-constraints) enabled

!!! tip "Usage"

    ```bash
    datamodel-codegen --input annotated.graphql --output-model-type pydantic_v2.BaseModel --use-annotated # (1)!
    ```

    1. :material-arrow-left: `--use-annotated` - the option documented here

??? example "Examples"

    === "OpenAPI"

        **Input Schema:**

        ```yaml
        openapi: "3.0.0"
        info:
          version: 1.0.0
          title: Swagger Petstore
          license:
            name: MIT
        servers:
          - url: http://petstore.swagger.io/v1
        paths:
          /pets:
            get:
              summary: List all pets
              operationId: listPets
              tags:
                - pets
              parameters:
                - name: limit
                  in: query
                  description: How many items to return at one time (max 100)
                  required: false
                  schema:
                    type: integer
                    format: int32
                    minimum: 0
                    maximum: 100
              responses:
                '200':
                  description: A paged array of pets
                  headers:
                    x-next:
                      description: A link to the next page of responses
                      schema:
                        type: string
                  content:
                    application/json:
                      schema:
                        $ref: "#/components/schemas/Pets"
                default:
                  description: unexpected error
                  content:
                    application/json:
                      schema:
                        $ref: "#/components/schemas/Error"
                        x-amazon-apigateway-integration:
                          uri:
                            Fn::Sub: arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${PythonVersionFunction.Arn}/invocations
                          passthroughBehavior: when_no_templates
                          httpMethod: POST
                          type: aws_proxy
            post:
              summary: Create a pet
              operationId: createPets
              tags:
                - pets
              responses:
                '201':
                  description: Null response
                default:
                  description: unexpected error
                  content:
                    application/json:
                      schema:
                        $ref: "#/components/schemas/Error"
                        x-amazon-apigateway-integration:
                          uri:
                            Fn::Sub: arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${PythonVersionFunction.Arn}/invocations
                          passthroughBehavior: when_no_templates
                          httpMethod: POST
                          type: aws_proxy
          /pets/{petId}:
            get:
              summary: Info for a specific pet
              operationId: showPetById
              tags:
                - pets
              parameters:
                - name: petId
                  in: path
                  required: true
                  description: The id of the pet to retrieve
                  schema:
                    type: string
              responses:
                '200':
                  description: Expected response to a valid request
                  content:
                    application/json:
                      schema:
                        $ref: "#/components/schemas/Pets"
                default:
                  description: unexpected error
                  content:
                    application/json:
                      schema:
                        $ref: "#/components/schemas/Error"
            x-amazon-apigateway-integration:
              uri:
                Fn::Sub: arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${PythonVersionFunction.Arn}/invocations
              passthroughBehavior: when_no_templates
              httpMethod: POST
              type: aws_proxy
        components:
          schemas:
            Pet:
              required:
                - id
                - name
              properties:
                id:
                  type: integer
                  format: int64
                  minimum: 0
                  maximum: 9223372036854775807
                name:
                  type: string
                  maxLength: 256
                tag:
                  type: string
                  maxLength: 64
            Pets:
              type: array
              items:
                $ref: "#/components/schemas/Pet"
              maxItems: 10
              minItems: 1
              uniqueItems: true
            UID:
              type: integer
              minimum: 0
            Users:
              type: array
              items:
                required:
                  - id
                  - name
                  - uid
                properties:
                  id:
                    type: integer
                    format: int64
                    minimum: 0
                  name:
                    type: string
                    maxLength: 256
                  tag:
                    type: string
                    maxLength: 64
                  uid:
                    $ref: '#/components/schemas/UID'
                  phones:
                    type: array
                    items:
                      type: string
                      minLength: 3
                    maxItems: 10
                  fax:
                    type: array
                    items:
                      type: string
                      minLength: 3
                  height:
                    type:
                      - integer
                      - number
                    minimum: 1
                    maximum: 300
                  weight:
                    type:
                      - number
                      - integer
                    minimum: 1.0
                    maximum: 1000.0
                  age:
                    type: integer
                    minimum: 0.0
                    maximum: 200.0
                    exclusiveMinimum: true
                  rating:
                    type: number
                    minimum: 0
                    exclusiveMinimum: true
                    maximum: 5
        
            Id:
              type: string
            Rules:
              type: array
              items:
                type: string
            Error:
              required:
                - code
                - message
              properties:
                code:
                  type: integer
                  format: int32
                message:
                  type: string
            apis:
              type: array
              items:
                type: object
                properties:
                  apiKey:
                    type: string
                    description: To be used as a dataset parameter value
                  apiVersionNumber:
                    type: string
                    description: To be used as a version parameter value
                  apiUrl:
                    type: string
                    format: uri
                    minLength: 1
                    description: "The URL describing the dataset's fields"
                  apiDocumentationUrl:
                    type: string
                    format: uri
                    description: A URL to the API console for each API
            Event:
              type: object
              properties:
                name:
                  type: string
            Result:
                type: object
                properties:
                  event:
                    $ref: '#/components/schemas/Event'
        ```

        **Output:**

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  api_constrained.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Annotated

        from pydantic import AnyUrl, BaseModel, Field, RootModel


        class Pet(BaseModel):
            id: Annotated[int, Field(ge=0, le=9223372036854775807)]
            name: Annotated[str, Field(max_length=256)]
            tag: Annotated[str | None, Field(max_length=64)] = None


        class Pets(RootModel[list[Pet]]):
            root: Annotated[list[Pet], Field(max_length=10, min_length=1)]


        class UID(RootModel[int]):
            root: Annotated[int, Field(ge=0)]


        class Phone(RootModel[str]):
            root: Annotated[str, Field(min_length=3)]


        class FaxItem(RootModel[str]):
            root: Annotated[str, Field(min_length=3)]


        class User(BaseModel):
            id: Annotated[int, Field(ge=0)]
            name: Annotated[str, Field(max_length=256)]
            tag: Annotated[str | None, Field(max_length=64)] = None
            uid: UID
            phones: Annotated[list[Phone] | None, Field(max_length=10)] = None
            fax: list[FaxItem] | None = None
            height: Annotated[int | float | None, Field(ge=1.0, le=300.0)] = None
            weight: Annotated[float | int | None, Field(ge=1.0, le=1000.0)] = None
            age: Annotated[int | None, Field(gt=0, le=200)] = None
            rating: Annotated[float | None, Field(gt=0.0, le=5.0)] = None


        class Users(RootModel[list[User]]):
            root: list[User]


        class Id(RootModel[str]):
            root: str


        class Rules(RootModel[list[str]]):
            root: list[str]


        class Error(BaseModel):
            code: int
            message: str


        class Api(BaseModel):
            apiKey: Annotated[
                str | None, Field(description='To be used as a dataset parameter value')
            ] = None
            apiVersionNumber: Annotated[
                str | None, Field(description='To be used as a version parameter value')
            ] = None
            apiUrl: Annotated[
                AnyUrl | None, Field(description="The URL describing the dataset's fields")
            ] = None
            apiDocumentationUrl: Annotated[
                AnyUrl | None, Field(description='A URL to the API console for each API')
            ] = None


        class Apis(RootModel[list[Api]]):
            root: list[Api]


        class Event(BaseModel):
            name: str | None = None


        class Result(BaseModel):
            event: Event | None = None
        ```

        <!-- fmt: on -->

    === "GraphQL"

        **Input Schema:**

        ```graphql
        type A {
            field: String!
            optionalField: String
            listField: [String!]!
            listOptionalField: [String]!
            optionalListField: [String!]
            optionalListOptionalField: [String]
            listListField:[[String!]!]!
        }
        ```

        **Output:**

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  annotated.graphql
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Annotated, Literal

        from pydantic import BaseModel, Field
        from typing_extensions import TypeAliasType

        Boolean = TypeAliasType("Boolean", bool)
        """
        The `Boolean` scalar type represents `true` or `false`.
        """


        String = TypeAliasType("String", str)
        """
        The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
        """


        class A(BaseModel):
            field: String
            listField: list[String]
            listListField: list[list[String]]
            listOptionalField: list[String | None]
            optionalField: String | None = None
            optionalListField: list[String] | None = None
            optionalListOptionalField: list[String | None] | None = None
            typename__: Annotated[Literal['A'] | None, Field(alias='__typename')] = 'A'

            @property
            def __typename(self) -> Literal['A'] | None:
                return self.typename__
        ```

        <!-- fmt: on -->

---

## `--use-bytes-for-content-media-type` {#use-bytes-for-content-media-type}

Type an OpenAPI string with a `contentMediaType` and no `contentEncoding` as `bytes`.

OpenAPI 3.1 writes raw binary content, such as an uploaded image, as a string with a `contentMediaType` and no
`contentEncoding`, in place of 3.0's `format: binary`, and the generated clients and servers send and receive such a
string as a file. Without this option the models type it `str`, so a generated server refuses every upload that is
not UTF-8. With it the string is `bytes`, as a `format: binary` string is, at a property, an item, a nullable property
and a root, with its length constraints; a string with a `contentEncoding` or a format of its own keeps its type.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input content_media_type_bytes.yaml --use-bytes-for-content-media-type # (1)!
    ```

    1. :material-arrow-left: `--use-bytes-for-content-media-type` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Raw binary content named by content media types
      version: "1"
    paths: {}
    components:
      schemas:
        Upload:
          type: object
          required: [image]
          properties:
            image: {type: string, contentMediaType: image/png}
            limited: {type: string, contentMediaType: text/plain, maxLength: 8}
            images: {type: array, items: {type: string, contentMediaType: image/png}}
            nullable: {type: [string, "null"], contentMediaType: application/pdf}
            picture: {$ref: "#/components/schemas/Picture"}
            encoded: {type: string, contentMediaType: image/png, contentEncoding: base64}
            email: {type: string, format: email, contentMediaType: text/plain}
            text: {type: string}
        Picture:
          type: string
          contentMediaType: image/jpeg
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  content_media_type_bytes.yaml

        from __future__ import annotations

        from pydantic import BaseModel, EmailStr, Field, RootModel, conbytes


        class Picture(RootModel[bytes]):
            root: bytes = Field(..., json_schema_extra={'contentMediaType': 'image/jpeg'})


        class Upload(BaseModel):
            image: bytes = Field(..., json_schema_extra={'contentMediaType': 'image/png'})
            limited: conbytes(max_length=8) | None = Field(
                None, json_schema_extra={'contentMediaType': 'text/plain'}
            )
            images: list[bytes] | None = None
            nullable: bytes | None = Field(
                None, json_schema_extra={'contentMediaType': 'application/pdf'}
            )
            picture: Picture | None = None
            encoded: str | None = Field(
                None,
                json_schema_extra={
                    'contentMediaType': 'image/png',
                    'contentEncoding': 'base64',
                },
            )
            email: EmailStr | None = Field(
                None, json_schema_extra={'contentMediaType': 'text/plain'}
            )
            text: str | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  content_media_type_bytes.yaml

        from __future__ import annotations

        from pydantic import BaseModel, EmailStr, Field, RootModel, constr


        class Picture(RootModel[str]):
            root: str = Field(..., json_schema_extra={'contentMediaType': 'image/jpeg'})


        class Upload(BaseModel):
            image: str = Field(..., json_schema_extra={'contentMediaType': 'image/png'})
            limited: constr(max_length=8) | None = Field(
                None, json_schema_extra={'contentMediaType': 'text/plain'}
            )
            images: list[str] | None = None
            nullable: str | None = Field(
                None, json_schema_extra={'contentMediaType': 'application/pdf'}
            )
            picture: Picture | None = None
            encoded: str | None = Field(
                None,
                json_schema_extra={
                    'contentMediaType': 'image/png',
                    'contentEncoding': 'base64',
                },
            )
            email: EmailStr | None = Field(
                None, json_schema_extra={'contentMediaType': 'text/plain'}
            )
            text: str | None = None
        ```

        <!-- fmt: on -->

---

## `--use-closed-typed-dict` {#use-closed-typed-dict}

Generate TypedDict with PEP 728 closed/extra_items (default: enabled).

When enabled (default), generates TypedDict with PEP 728 `closed=True` or `extra_items`
parameters based on `additionalProperties` constraints in the schema.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input typed_dict_closed.json --output-model-type typing.TypedDict --use-closed-typed-dict # (1)!
    ```

    1. :material-arrow-left: `--use-closed-typed-dict` - the option documented here

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: cli-reference.typing-customization.use-closed-typed-dict.example -->
??? example "Examples"

    **Input Schema:**
    <!-- fmt: off -->

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "object",
      "title": "ClosedModel",
      "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"}
      },
      "required": ["name"],
      "additionalProperties": false
    }
    ```

    <!-- fmt: on -->

    **Output:**
    <!-- fmt: off -->

    ```python
    from __future__ import annotations

    from typing_extensions import NotRequired, TypedDict


    class ClosedModel(TypedDict, closed=True):
        name: str
        age: NotRequired[int]
    ```

    <!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: cli-reference.typing-customization.use-closed-typed-dict.example -->

---

## `--use-decimal-for-multiple-of` {#use-decimal-for-multiple-of}

Generate Decimal types for fields with multipleOf constraint.

The `--use-decimal-for-multiple-of` flag generates `condecimal` or `Decimal`
types for numeric fields that have a `multipleOf` constraint. This ensures
precise decimal arithmetic when validating values against the constraint.

**See also:** [Type Mappings and Custom Types](../type-mappings.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input use_decimal_for_multiple_of.json --use-decimal-for-multiple-of # (1)!
    ```

    1. :material-arrow-left: `--use-decimal-for-multiple-of` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "type": "object",
      "properties": {
        "price": {
          "type": "number",
          "multipleOf": 0.01,
          "minimum": 0,
          "maximum": 99999.99
        },
        "quantity": {
          "type": "number",
          "multipleOf": 0.1
        },
        "rate": {
          "type": "number",
          "multipleOf": 0.001,
          "exclusiveMinimum": 0,
          "exclusiveMaximum": 1
        },
        "simple_float": {
          "type": "number",
          "minimum": 0,
          "maximum": 100
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  use_decimal_for_multiple_of.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from decimal import Decimal

    from pydantic import BaseModel, condecimal, confloat


    class Model(BaseModel):
        price: (
            condecimal(ge=Decimal('0'), le=Decimal('99999.99'), multiple_of=Decimal('0.01'))
            | None
        ) = None
        quantity: condecimal(multiple_of=Decimal('0.1')) | None = None
        rate: (
            condecimal(multiple_of=Decimal('0.001'), lt=Decimal('1.0'), gt=Decimal('0.0'))
            | None
        ) = None
        simple_float: confloat(ge=0.0, le=100.0) | None = None
    ```

    <!-- fmt: on -->

---

## `--use-decimal-in-field-forms` {#use-decimal-in-field-forms}

Write `Decimal` for a number with `multipleOf` in the field forms too.

Under `--use-decimal-for-multiple-of`, the field forms (`--field-constraints`, `--use-annotated`) write a `float` with
the constraints in `Field()`, which accepts numbers that are no multiples within the float tolerance, such as
`0.0100000001` for `multipleOf: 0.01`. With this option they write a `Decimal` with the constraints in `Field()`
instead, at a property, an item, a map value and a root, which rejects them as `condecimal` does. Under
`--strict-types float` the `Decimal` is strict: `condecimal(..., strict=True)`, or `strict=True` among the `Field()`
arguments.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input decimal_multiple_of_field_forms.json --use-decimal-for-multiple-of --use-annotated --use-decimal-in-field-forms # (1)!
    ```

    1. :material-arrow-left: `--use-decimal-in-field-forms` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "title": "DecimalMultipleOfFieldForms",
      "type": "object",
      "properties": {
        "amount": {"type": "number", "multipleOf": 0.01, "minimum": 0, "maximum": 1000000},
        "opt": {"type": ["number", "null"], "multipleOf": 0.5, "minimum": -1000000, "maximum": 1000000},
        "maxed": {"type": "number", "multipleOf": 0.1, "minimum": -1000000, "maximum": 10},
        "inty": {"type": "integer", "multipleOf": 2},
        "arr": {"type": "array", "items": {"type": "number", "multipleOf": 0.01, "minimum": -1000000, "maximum": 1000000}},
        "mapv": {"type": "object", "additionalProperties": {"type": "number", "multipleOf": 0.01, "minimum": -1000000, "maximum": 1000000}},
        "rate": {"$ref": "#/$defs/Rate"},
        "plainf": {"type": "number", "minimum": 0}
      },
      "required": ["amount"],
      "$defs": {"Rate": {"type": "number", "multipleOf": 0.001, "exclusiveMinimum": 0, "maximum": 1000000}}
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  decimal_multiple_of_field_forms.json

        from __future__ import annotations

        from decimal import Decimal
        from typing import Annotated

        from pydantic import BaseModel, Field, RootModel
        from typing_extensions import TypeAliasType


        class ArrItem(RootModel[Decimal]):
            root: Annotated[Decimal, Field(ge=-1000000, le=1000000, multiple_of=0.01)]


        MapvAdditionalProperty = TypeAliasType(
            "MapvAdditionalProperty",
            Annotated[Decimal, Field(ge=-1000000, le=1000000, multiple_of=0.01)],
        )


        class Rate(RootModel[Decimal]):
            root: Annotated[Decimal, Field(gt=0, le=1000000, multiple_of=0.001)]


        class DecimalMultipleOfFieldForms(BaseModel):
            amount: Annotated[Decimal, Field(ge=0, le=1000000, multiple_of=0.01)]
            opt: Annotated[Decimal | None, Field(ge=-1000000, le=1000000, multiple_of=0.5)] = (
                None
            )
            maxed: Annotated[Decimal | None, Field(ge=-1000000, le=10, multiple_of=0.1)] = None
            inty: Annotated[int | None, Field(multiple_of=2)] = None
            arr: list[ArrItem] | None = None
            mapv: dict[str, MapvAdditionalProperty] | None = None
            rate: Rate | None = None
            plainf: Annotated[float | None, Field(ge=0.0)] = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  decimal_multiple_of_field_forms.json

        from __future__ import annotations

        from typing import Annotated

        from pydantic import BaseModel, Field, RootModel
        from typing_extensions import TypeAliasType


        class ArrItem(RootModel[float]):
            root: Annotated[float, Field(ge=-1000000.0, le=1000000.0, multiple_of=0.01)]


        MapvAdditionalProperty = TypeAliasType(
            "MapvAdditionalProperty",
            Annotated[float, Field(ge=-1000000.0, le=1000000.0, multiple_of=0.01)],
        )


        class Rate(RootModel[float]):
            root: Annotated[float, Field(gt=0.0, le=1000000.0, multiple_of=0.001)]


        class DecimalMultipleOfFieldForms(BaseModel):
            amount: Annotated[float, Field(ge=0.0, le=1000000.0, multiple_of=0.01)]
            opt: Annotated[
                float | None, Field(ge=-1000000.0, le=1000000.0, multiple_of=0.5)
            ] = None
            maxed: Annotated[float | None, Field(ge=-1000000.0, le=10.0, multiple_of=0.1)] = (
                None
            )
            inty: Annotated[int | None, Field(multiple_of=2)] = None
            arr: list[ArrItem] | None = None
            mapv: dict[str, MapvAdditionalProperty] | None = None
            rate: Rate | None = None
            plainf: Annotated[float | None, Field(ge=0.0)] = None
        ```

        <!-- fmt: on -->

---

## `--use-encoded-str-for-constrained-base64` {#use-encoded-str-for-constrained-base64}

Check the constraints of a `format: byte` string on its base64 text in the field forms.

JSON Schema checks `pattern`, `minLength` and `maxLength` of a `format: byte` string on the base64 text. The field
forms write `Annotated[Base64Str, Field(...)]` (`--use-annotated`) or, for an optional field, `Base64Str | None =
Field(...)` (`--field-constraints`), and Pydantic checks such constraints on the decoded text: `maxLength: 8` accepts
the 12 characters of `YWFhYWFhYQ==`, which decode to 7. With this option the string is written
`Annotated[str, Field(...), EncodedStr(encoder=Base64Encoder)]`, which Pydantic 2.10 or later checks on the text before
decoding it, at a property, an item, a map value, a root and a union member; Pydantic before 2.10 checks none of these
forms on the text, and a target Pydantic older than 2.10 warns so.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input encoded_str_for_constrained_base64.json --use-annotated --use-encoded-str-for-constrained-base64 # (1)!
    ```

    1. :material-arrow-left: `--use-encoded-str-for-constrained-base64` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "EncodedStrForConstrainedBase64",
      "type": "object",
      "required": ["required"],
      "properties": {
        "pattern": {"type": "string", "format": "byte", "pattern": "^Y"},
        "lengths": {"type": "string", "format": "byte", "minLength": 4, "maxLength": 8},
        "required": {"type": "string", "format": "byte", "minLength": 4},
        "nullable": {"type": ["string", "null"], "format": "byte", "maxLength": 8},
        "plain": {"type": "string", "format": "byte"},
        "items": {"type": "array", "items": {"type": "string", "format": "byte", "maxLength": 8}},
        "values": {"type": "object", "additionalProperties": {"type": "string", "format": "byte", "pattern": "^Y"}},
        "ref": {"$ref": "#/$defs/Short"},
        "member": {"anyOf": [{"type": "string", "format": "byte", "maxLength": 8}, {"type": "integer"}]},
        "branch": {"type": ["array", "string"], "format": "byte", "maxLength": 8, "items": {"type": "integer"}}
      },
      "$defs": {
        "Short": {"type": "string", "format": "byte", "maxLength": 8}
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  encoded_str_for_constrained_base64.json

        from __future__ import annotations

        from typing import Annotated

        from pydantic import Base64Encoder, Base64Str, BaseModel, EncodedStr, Field, RootModel
        from typing_extensions import TypeAliasType


        class Item(
            RootModel[Annotated[str, Field(max_length=8), EncodedStr(encoder=Base64Encoder)]]
        ):
            root: Annotated[str, Field(max_length=8), EncodedStr(encoder=Base64Encoder)]


        ValuesAdditionalProperty = TypeAliasType(
            "ValuesAdditionalProperty",
            Annotated[str, Field(pattern=r'^Y'), EncodedStr(encoder=Base64Encoder)],
        )


        class Member(
            RootModel[Annotated[str, Field(max_length=8), EncodedStr(encoder=Base64Encoder)]]
        ):
            root: Annotated[str, Field(max_length=8), EncodedStr(encoder=Base64Encoder)]


        BranchString = TypeAliasType(
            "BranchString",
            Annotated[str, Field(max_length=8), EncodedStr(encoder=Base64Encoder)],
        )


        class Short(
            RootModel[Annotated[str, Field(max_length=8), EncodedStr(encoder=Base64Encoder)]]
        ):
            root: Annotated[str, Field(max_length=8), EncodedStr(encoder=Base64Encoder)]


        class EncodedStrForConstrainedBase64(BaseModel):
            pattern: (
                Annotated[str, Field(pattern=r'^Y'), EncodedStr(encoder=Base64Encoder)] | None
            ) = None
            lengths: (
                Annotated[
                    str, Field(min_length=4, max_length=8), EncodedStr(encoder=Base64Encoder)
                ]
                | None
            ) = None
            required: Annotated[str, Field(min_length=4), EncodedStr(encoder=Base64Encoder)]
            nullable: (
                Annotated[str, Field(max_length=8), EncodedStr(encoder=Base64Encoder)] | None
            ) = None
            plain: Base64Str | None = None
            items: list[Item] | None = None
            values: dict[str, ValuesAdditionalProperty] | None = None
            ref: Short | None = None
            member: Member | int | None = None
            branch: BranchString | list[int] | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  encoded_str_for_constrained_base64.json

        from __future__ import annotations

        from typing import Annotated

        from pydantic import Base64Str, BaseModel, Field, RootModel
        from typing_extensions import TypeAliasType


        class Item(RootModel[Base64Str]):
            root: Annotated[Base64Str, Field(max_length=8)]


        ValuesAdditionalProperty = TypeAliasType(
            "ValuesAdditionalProperty", Annotated[Base64Str, Field(pattern='^Y')]
        )


        class Member(RootModel[Base64Str]):
            root: Annotated[Base64Str, Field(max_length=8)]


        BranchString = TypeAliasType("BranchString", Annotated[Base64Str, Field(max_length=8)])


        class Short(RootModel[Base64Str]):
            root: Annotated[Base64Str, Field(max_length=8)]


        class EncodedStrForConstrainedBase64(BaseModel):
            pattern: Annotated[Base64Str | None, Field(pattern='^Y')] = None
            lengths: Annotated[Base64Str | None, Field(max_length=8, min_length=4)] = None
            required: Annotated[Base64Str, Field(min_length=4)]
            nullable: Annotated[Base64Str | None, Field(max_length=8)] = None
            plain: Base64Str | None = None
            items: list[Item] | None = None
            values: dict[str, ValuesAdditionalProperty] | None = None
            ref: Short | None = None
            member: Member | int | None = None
            branch: BranchString | list[int] | None = None
        ```

        <!-- fmt: on -->

---

## `--use-generic-container-types` {#use-generic-container-types}

Use generic container types (Sequence, Mapping) for type hinting.

The `--use-generic-container-types` flag generates abstract container types
(Sequence, Mapping, FrozenSet) instead of concrete types (list, dict, set).
If `--use-standard-collections` is set, imports from `collections.abc`;
otherwise imports from `typing`.

**Related:** [`--use-standard-collections`](#use-standard-collections)

**See also:** [Python Version Compatibility](../python-version-compatibility.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input root_model_with_additional_properties.json --use-generic-container-types # (1)!
    ```

    1. :material-arrow-left: `--use-generic-container-types` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "$id": "test.json",
      "description": "test",
      "type": "object",
      "required": [
        "test_id",
        "test_ip",
        "result",
        "nested_object_result",
        "nested_enum_result"
      ],
      "properties": {
        "test_id": {
          "type": "string",
          "description": "test ID"
        },
        "test_ip": {
          "type": "string",
          "description": "test IP"
        },
        "result": {
          "type": "object",
          "additionalProperties": {
            "type": "integer"
          }
        },
        "nested_object_result": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "properties": {
              "status":{
                "type": "integer"
              }
            },
            "required": ["status"]
          }
        },
        "nested_enum_result": {
          "type": "object",
          "additionalProperties": {
            "enum": ["red", "green"]
          }
        },
        "all_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "allOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "one_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "oneOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "any_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "anyOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "all_of_with_unknown_object" :{
          "type" : "object",
          "additionalProperties" :
          {
            "allOf" : [
              { "$ref" : "#/definitions/User" },
              { "description": "TODO" }
            ]
          }
        },
        "objectRef": {
          "type": "object",
          "additionalProperties": {
            "$ref": "#/definitions/User"
          }
        },
        "deepNestedObjectRef": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "additionalProperties": {
              "type": "object",
              "additionalProperties": {
                 "$ref": "#/definitions/User"
              }
            }
          }
        }
      },
      "definitions": {
        "User": {
          "type": "object",
          "properties": {
            "name": {
              "type": "string"
            }
          }
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  root_model_with_additional_properties.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from collections.abc import Mapping
    from enum import Enum

    from pydantic import BaseModel, Field


    class NestedObjectResult(BaseModel):
        status: int


    class NestedEnumResult(Enum):
        red = 'red'
        green = 'green'


    class OneOfResult(BaseModel):
        description: str | None = None


    class AnyOfResult(BaseModel):
        description: str | None = None


    class User(BaseModel):
        name: str | None = None


    class AllOfResult(User):
        description: str | None = None


    class Model(BaseModel):
        test_id: str = Field(..., description='test ID')
        test_ip: str = Field(..., description='test IP')
        result: Mapping[str, int]
        nested_object_result: Mapping[str, NestedObjectResult]
        nested_enum_result: Mapping[str, NestedEnumResult]
        all_of_result: Mapping[str, AllOfResult] | None = None
        one_of_result: Mapping[str, User | OneOfResult] | None = None
        any_of_result: Mapping[str, User | AnyOfResult] | None = None
        all_of_with_unknown_object: Mapping[str, User] | None = None
        objectRef: Mapping[str, User] | None = None
        deepNestedObjectRef: Mapping[str, Mapping[str, Mapping[str, User]]] | None = None
    ```

    <!-- fmt: on -->

---

## `--use-non-positive-negative-number-constrained-types` {#use-non-positive-negative-number-constrained-types}

Use NonPositive/NonNegative types for number constraints.

The `--use-non-positive-negative-number-constrained-types` flag generates
Pydantic's NonPositiveInt, NonNegativeInt, NonPositiveFloat, and NonNegativeFloat
types for fields with minimum: 0 or maximum: 0 constraints, instead of using
conint/confloat with ge/le parameters.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input use_non_positive_negative.json --use-non-positive-negative-number-constrained-types # (1)!
    ```

    1. :material-arrow-left: `--use-non-positive-negative-number-constrained-types` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "NumberConstraints",
      "type": "object",
      "properties": {
        "non_negative_int": {
          "type": "integer",
          "minimum": 0,
          "description": "NonNegativeInt: minimum=0 only"
        },
        "non_positive_int": {
          "type": "integer",
          "maximum": 0,
          "description": "NonPositiveInt: maximum=0 only"
        },
        "non_negative_float": {
          "type": "number",
          "minimum": 0,
          "description": "NonNegativeFloat: minimum=0 only"
        },
        "non_positive_float": {
          "type": "number",
          "maximum": 0,
          "description": "NonPositiveFloat: maximum=0 only"
        },
        "positive_int": {
          "type": "integer",
          "exclusiveMinimum": 0,
          "description": "PositiveInt: exclusiveMinimum=0"
        },
        "negative_int": {
          "type": "integer",
          "exclusiveMaximum": 0,
          "description": "NegativeInt: exclusiveMaximum=0"
        },
        "positive_float": {
          "type": "number",
          "exclusiveMinimum": 0,
          "description": "PositiveFloat: exclusiveMinimum=0"
        },
        "negative_float": {
          "type": "number",
          "exclusiveMaximum": 0,
          "description": "NegativeFloat: exclusiveMaximum=0"
        },
        "bounded_non_negative_int": {
          "type": "integer",
          "minimum": 0,
          "maximum": 100,
          "description": "NonNegativeInt with additional upper bound"
        },
        "bounded_non_positive_int": {
          "type": "integer",
          "maximum": 0,
          "minimum": -100,
          "description": "NonPositiveInt with additional lower bound"
        },
        "bounded_non_negative_float": {
          "type": "number",
          "minimum": 0,
          "maximum": 1.0,
          "description": "NonNegativeFloat with additional upper bound"
        },
        "bounded_non_positive_float": {
          "type": "number",
          "maximum": 0,
          "minimum": -1.0,
          "description": "NonPositiveFloat with additional lower bound"
        },
        "plain_constrained_int": {
          "type": "integer",
          "minimum": 5,
          "maximum": 100,
          "description": "No zero bound: should remain conint/Field"
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  use_non_positive_negative.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import (
        BaseModel,
        Field,
        NegativeFloat,
        NegativeInt,
        NonNegativeFloat,
        NonNegativeInt,
        NonPositiveFloat,
        NonPositiveInt,
        PositiveFloat,
        PositiveInt,
        confloat,
        conint,
    )


    class NumberConstraints(BaseModel):
        non_negative_int: NonNegativeInt | None = Field(
            None, description='NonNegativeInt: minimum=0 only'
        )
        non_positive_int: NonPositiveInt | None = Field(
            None, description='NonPositiveInt: maximum=0 only'
        )
        non_negative_float: NonNegativeFloat | None = Field(
            None, description='NonNegativeFloat: minimum=0 only'
        )
        non_positive_float: NonPositiveFloat | None = Field(
            None, description='NonPositiveFloat: maximum=0 only'
        )
        positive_int: PositiveInt | None = Field(
            None, description='PositiveInt: exclusiveMinimum=0'
        )
        negative_int: NegativeInt | None = Field(
            None, description='NegativeInt: exclusiveMaximum=0'
        )
        positive_float: PositiveFloat | None = Field(
            None, description='PositiveFloat: exclusiveMinimum=0'
        )
        negative_float: NegativeFloat | None = Field(
            None, description='NegativeFloat: exclusiveMaximum=0'
        )
        bounded_non_negative_int: conint(ge=0, le=100) | None = Field(
            None, description='NonNegativeInt with additional upper bound'
        )
        bounded_non_positive_int: conint(ge=-100, le=0) | None = Field(
            None, description='NonPositiveInt with additional lower bound'
        )
        bounded_non_negative_float: confloat(ge=0.0, le=1.0) | None = Field(
            None, description='NonNegativeFloat with additional upper bound'
        )
        bounded_non_positive_float: confloat(ge=-1.0, le=0.0) | None = Field(
            None, description='NonPositiveFloat with additional lower bound'
        )
        plain_constrained_int: conint(ge=5, le=100) | None = Field(
            None, description='No zero bound: should remain conint/Field'
        )
    ```

    <!-- fmt: on -->

---

## `--use-object-type` {#use-object-type}

Use object instead of Any for unspecified object and array values.

The `--use-object-type` flag configures the code generation to use `object`
instead of `Any` when JSON Schema does not define object properties or array
item schemas. This helps generate stricter type hints for unstructured values.

**Related:** [`--output-model-type`](model-customization.md#output-model-type), [`--target-python-version`](model-customization.md#target-python-version)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input use_object_type.json --output-model-type typing.TypedDict --target-python-version 3.10 --use-object-type # (1)!
    ```

    1. :material-arrow-left: `--use-object-type` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "Payload",
      "type": "object",
      "properties": {
        "metadata": {
          "type": "object"
        },
        "items": {
          "type": "array"
        },
        "nested": {
          "type": "array",
          "items": {
            "type": "object"
          }
        }
      },
      "required": ["metadata", "items", "nested"]
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  use_object_type.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import TypedDict


    class Payload(TypedDict):
        metadata: dict[str, object]
        items: list[object]
        nested: list[dict[str, object]]
    ```

    <!-- fmt: on -->

---

## `--use-pendulum` {#use-pendulum}

Use pendulum types for date, time, and duration fields.

The `--use-pendulum` flag maps schema `date`, `time`, and `duration` values to
Pendulum types such as `pendulum.Date`, `pendulum.Time`, and `pendulum.Duration`.
`date-time` fields continue to use `pydantic.AwareDatetime`.

If you need a different datetime class for `date-time` fields, use
[`--output-datetime-class`](#output-datetime-class).

**See also:** [Type Mappings and Custom Types](../type-mappings.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input use_pendulum.json --use-pendulum # (1)!
    ```

    1. :material-arrow-left: `--use-pendulum` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Event",
      "type": "object",
      "properties": {
        "name": {
          "type": "string"
        },
        "created_at": {
          "type": "string",
          "format": "date-time"
        },
        "event_date": {
          "type": "string",
          "format": "date"
        },
        "duration": {
          "type": "string",
          "format": "duration"
        }
      },
      "required": ["name", "created_at"]
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  use_pendulum.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pendulum import Date, Duration
    from pydantic import AwareDatetime, BaseModel


    class Event(BaseModel):
        name: str
        created_at: AwareDatetime
        event_date: Date | None = None
        duration: Duration | None = None
    ```

    <!-- fmt: on -->

---

## `--use-plain-alias-for-recursive-models` {#use-plain-alias-for-recursive-models}

Write the type alias of a model that names itself as a plain alias in Pydantic output.

A type alias whose whole value is a model that names itself, such as `Alias = TypeAliasType("Alias", Node)` for
`Node.next: Node | None`, is refused by Pydantic 2 in the second model that uses it: the module fails to import
(circular-reference-schema), or, where the alias carries metadata, that model fails to validate. With
`--use-plain-alias-for-recursive-models` the alias is written as `Alias: TypeAlias = Node`, the model itself, which
every model that uses it builds; it then has no entry of its own in `model_json_schema()` `$defs`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input recursive_model_alias_two_users.json --output-model-type pydantic_v2.dataclass --use-plain-alias-for-recursive-models # (1)!
    ```

    1. :material-arrow-left: `--use-plain-alias-for-recursive-models` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$defs": {
        "Node": {
          "type": "object",
          "properties": {
            "next": {"$ref": "#/$defs/Node"}
          }
        },
        "Alias": {
          "$ref": "#/$defs/Node",
          "properties": {
            "a": {"type": "string"}
          }
        },
        "Described": {
          "$ref": "#/$defs/Node",
          "description": "A node beside a description"
        },
        "Solo": {
          "$ref": "#/$defs/Node",
          "description": "A node that one class uses"
        },
        "A": {
          "type": "object",
          "properties": {
            "n": {"$ref": "#/$defs/Alias"},
            "d": {"$ref": "#/$defs/Described"},
            "s": {"$ref": "#/$defs/Solo"}
          }
        },
        "B": {
          "type": "object",
          "properties": {
            "n": {"$ref": "#/$defs/Alias"},
            "d": {"$ref": "#/$defs/Described"}
          }
        }
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  recursive_model_alias_two_users.json

        from __future__ import annotations

        from typing import Annotated, Any, TypeAlias

        from pydantic import Field
        from pydantic.dataclasses import dataclass
        from typing_extensions import TypeAliasType

        Model = TypeAliasType("Model", Any)


        @dataclass
        class Node:
            next: Node | None = None


        Alias: TypeAlias = Node


        Described: TypeAlias = Annotated[
            Node, Field(..., description='A node beside a description')
        ]


        Solo: TypeAlias = Annotated[Node, Field(..., description='A node that one class uses')]


        @dataclass
        class A:
            n: Alias | None = None
            d: Described | None = None
            s: Solo | None = None


        @dataclass
        class B:
            n: Alias | None = None
            d: Described | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  recursive_model_alias_two_users.json

        from __future__ import annotations

        from typing import Annotated, Any

        from pydantic import Field
        from pydantic.dataclasses import dataclass
        from typing_extensions import TypeAliasType

        Model = TypeAliasType("Model", Any)


        @dataclass
        class Node:
            next: Node | None = None


        Alias = TypeAliasType("Alias", Node)


        Described = TypeAliasType(
            "Described", Annotated[Node, Field(..., description='A node beside a description')]
        )


        Solo = TypeAliasType(
            "Solo", Annotated[Node, Field(..., description='A node that one class uses')]
        )


        @dataclass
        class A:
            n: Alias | None = None
            d: Described | None = None
            s: Solo | None = None


        @dataclass
        class B:
            n: Alias | None = None
            d: Described | None = None
        ```

        <!-- fmt: on -->

---

## `--use-root-model-type-alias` {#use-root-model-type-alias}

Generate RootModel as type alias format for better mypy support.

When enabled, root models with simple types are generated as type aliases
instead of class definitions, improving mypy type inference.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input root_model_type_alias.json --use-root-model-type-alias --output-model-type pydantic_v2.BaseModel # (1)!
    ```

    1. :material-arrow-left: `--use-root-model-type-alias` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "definitions": {
        "Pet": {
          "type": "object",
          "properties": {
            "name": {"type": "string"}
          }
        },
        "Pets": {
          "oneOf": [
            {"$ref": "#/definitions/Pet"},
            {"type": "array", "items": {"$ref": "#/definitions/Pet"}}
          ]
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  root_model_type_alias.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel, RootModel

    Model = RootModel[Any]


    class Pet(BaseModel):
        name: str | None = None


    Pets = RootModel[Pet | list[Pet]]
    ```

    <!-- fmt: on -->

---

## `--use-single-dict-for-object-maps` {#use-single-dict-for-object-maps}

Type an object map as one dict instead of a union of dicts.

An object map is an object without `properties` whose keys `patternProperties` or `propertyNames` constrain.
With `--use-single-dict-for-object-maps` it is one dict whose key pattern joins every pattern and whose value is
the union of their value types, so an object that mixes the keys of several patterns is accepted; a dict that
cannot express the object exactly (keys outside the patterns, different value schemas) is named by a warning.
The null and the other types of a `type` array, such as `[object, string, null]`, join the dict in a union, and its
property counts stay on the dict; the dict root model of an object without `properties`, such as a nullable
`additionalProperties` map, takes the same null. The stricter pattern-map checks of `--generate-schema-validators`
are under `--check-pattern-maps-strictly`. Without `--use-single-dict-for-object-maps` the map is written as in
0.86.0: a union of dicts, one per value type of the patterns, the other types of a `type` array left out; msgspec,
which cannot decode a union of several dicts, gets the one dict.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input pattern_properties_merge.json --output-model-type pydantic_v2.BaseModel --use-single-dict-for-object-maps # (1)!
    ```

    1. :material-arrow-left: `--use-single-dict-for-object-maps` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "patternProperties": {
        "^i-": {
          "type": "string"
        },
        "^t-": {
          "type": "string"
        },
        "^n-": {
          "type": "integer"
        }
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  pattern_properties_merge.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import RootModel, constr


        class Model(RootModel[dict[constr(pattern=r'^i-|^t-|^n-'), str | int]]):
            root: dict[constr(pattern=r'^i-|^t-|^n-'), str | int]
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  pattern_properties_merge.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import RootModel, constr


        class Model(
            RootModel[dict[constr(pattern=r'^i-|^t-'), str] | dict[constr(pattern=r'^n-'), int]]
        ):
            root: dict[constr(pattern=r'^i-|^t-'), str] | dict[constr(pattern=r'^n-'), int]
        ```

        <!-- fmt: on -->

---

## `--use-single-value-enum-as-const` {#use-single-value-enum-as-const}

Treat `enum` with a single value as `const` with that value.

JSON Schema defines `{"enum": ["low"]}` as equal to `{"const": "low"}`. With
`--use-single-value-enum-as-const`, each schema in the JSON Schema, OpenAPI and AsyncAPI documents
whose `enum` lists one value is read as that `const`, so the output is the same as for the documents
written with `const`, while the enums that allOf intersections leave stay enums:
oneOf/anyOf branches of single-value enums, as Rust's schemars writes documented unit variants,
become one Enum with branch titles as member names when there are two or more and all hold values
of one JSON type; otherwise each branch is typed as written, such as `Literal['a'] | None` for one
value beside a `null` branch or `Literal['a'] | Literal[1]` for values of different JSON types. A
schema with one enum value is typed `Literal[...]` instead of a one-member Enum, except one with
`nullable: true` and a value other than `null`, which keeps its enum and is read as written. The
option reads `const` branches the same way, which without it merge into one Enum in those cases
too. Instance data such as `default` and `examples`, extension values (`x-...` except
`x-propertyNames`), links and servers stay as written, so a schema kept under an extension
key, such as one referenced as `headers.yaml#/x-rate`, is read as written too. A document parsed
with draft-04 schema features, which have no `const`, is kept, and Avro, Protocol Buffers, XML
Schema and GraphQL inputs are not changed.

**See also:** [Options That Replace Post-Processing](../post-processing-options.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-single-value-enum-as-const # (1)!
    ```

    1. :material-arrow-left: `--use-single-value-enum-as-const` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "Settings",
      "type": "object",
      "x-meta": {"enum": ["kept"]},
      "properties": {
        "level": {"$ref": "#/$defs/Level"},
        "phase": {"$ref": "#/$defs/Phase"},
        "code": {"$ref": "#/$defs/Code"},
        "mode": {"$ref": "#/$defs/Mode"},
        "source": {"$ref": "#/$defs/Source"},
        "region": {"$ref": "#/$defs/Region"},
        "method": {
          "type": "string",
          "enum": ["settings/update"],
          "x-meta": {"enum": ["kept"]}
        },
        "version": {"type": "integer", "enum": [2], "default": 2},
        "default": {"type": "string", "enum": ["named"]},
        "both": {"type": "string", "enum": ["same"], "const": "same"},
        "options": {
          "type": "object",
          "additionalProperties": true,
          "default": {"enum": ["kept"]},
          "examples": [{"enum": ["kept"]}]
        },
        "pick": {"$ref": "#/$defs/Pick"}
      },
      "required": ["method"],
      "x-generator": {"name": "schemars", "enum": ["kept"]},
      "$defs": {
        "Level": {
          "description": "How much effort to spend.",
          "oneOf": [
            {"type": "string", "enum": ["low"], "description": "Low effort."},
            {"type": "string", "enum": ["high"], "description": "High effort."}
          ]
        },
        "Phase": {
          "oneOf": [
            {"type": "string", "enum": ["commentary"], "title": "Commentary"},
            {"type": "string", "enum": ["final_answer"], "title": "FinalAnswer"}
          ]
        },
        "Code": {
          "oneOf": [
            {"type": "integer", "enum": [1], "description": "One."},
            {"type": "integer", "enum": [2], "description": "Two."}
          ]
        },
        "Mode": {
          "anyOf": [{"type": "string", "enum": ["fast"]}, {"type": "string", "enum": ["safe"]}, {"type": "null"}]
        },
        "Source": {
          "oneOf": [
            {"type": "string", "enum": ["user"]},
            {"type": "string", "const": "agent", "description": "Agent."}
          ]
        },
        "Region": {"type": "string", "enum": ["us"]},
        "AB": {"type": "string", "enum": ["a", "b"]},
        "Pick": {"allOf": [{"$ref": "#/$defs/AB", "enum": ["b", "c"]}]}
      }
    }
    ```

    **Output:**

    === "Pydantic v2"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  schema.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum, IntEnum
        from typing import Any, Literal

        from pydantic import BaseModel, Field, RootModel


        class Both(Enum):
            same = 'same'


        class Level(Enum):
            low = 'low'
            high = 'high'


        class Phase(Enum):
            Commentary = 'commentary'
            FinalAnswer = 'final_answer'


        class Code(IntEnum):
            integer_1 = 1
            integer_2 = 2


        class ModeEnum(Enum):
            fast = 'fast'
            safe = 'safe'


        class Mode(RootModel[ModeEnum | None]):
            root: ModeEnum | None = None


        class Source(Enum):
            user = 'user'
            agent = 'agent'


        class Region(RootModel[Literal['us']]):
            root: Literal['us']


        class AB(Enum):
            a = 'a'
            b = 'b'


        class Pick(Enum):
            b = 'b'


        class Settings(BaseModel):
            level: Level | None = None
            phase: Phase | None = None
            code: Code | None = None
            mode: Mode | None = None
            source: Source | None = None
            region: Region | None = None
            method: Literal['settings/update']
            version: Literal[2] = 2
            default: Literal['named'] | None = None
            both: Literal['same'] | None = None
            options: dict[str, Any] | None = Field(
                {'enum': ['kept']}, examples=[{'enum': ['kept']}]
            )
            pick: Pick | None = None
        ```

        <!-- fmt: on -->

    === "msgspec"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  schema.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum, IntEnum
        from typing import Annotated, Any, Literal, TypeAlias

        from msgspec import UNSET, Meta, Struct, UnsetType, field


        class Both(Enum):
            same = 'same'


        class Level(Enum):
            low = 'low'
            high = 'high'


        class Phase(Enum):
            Commentary = 'commentary'
            FinalAnswer = 'final_answer'


        class Code(IntEnum):
            integer_1 = 1
            integer_2 = 2


        class ModeEnum(Enum):
            fast = 'fast'
            safe = 'safe'


        Mode: TypeAlias = ModeEnum | None


        class Source(Enum):
            user = 'user'
            agent = 'agent'


        Region: TypeAlias = Literal['us']


        class AB(Enum):
            a = 'a'
            b = 'b'


        Pick: TypeAlias = AB


        class Settings(Struct):
            method: Literal['settings/update']
            level: Level | UnsetType = UNSET
            phase: Phase | UnsetType = UNSET
            code: Code | UnsetType = UNSET
            mode: Mode | UnsetType = UNSET
            source: Source | UnsetType = UNSET
            region: Region | UnsetType = UNSET
            version: Literal[2] | UnsetType = 2
            default: Literal['named'] | UnsetType = UNSET
            both: Both | UnsetType = UNSET
            options: (
                Annotated[dict[str, Any], Meta(examples=[{'enum': ['kept']}])] | UnsetType
            ) = field(default_factory=lambda: {'enum': ['kept']})
            pick: Pick | UnsetType = UNSET
        ```

        <!-- fmt: on -->

---

## `--use-specialized-enum` {#use-specialized-enum}

Generate StrEnum/IntEnum for string/integer enums (Python 3.11+).

The `--use-specialized-enum` flag generates specialized enum types:
- `StrEnum` for string enums
- `IntEnum` for integer enums

This is the default behavior for Python 3.11+ targets.

**Related:** [`--no-use-specialized-enum`](#no-use-specialized-enum), [`--use-subclass-enum`](model-customization.md#use-subclass-enum)

**See also:** [Options That Replace Post-Processing](../post-processing-options.md)

**Option relationships:**

- **Requires:** [`--target-python-version`](model-customization.md#target-python-version) = `3.11+` - `--use-specialized-enum` requires `--target-python-version` 3.11 or higher.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input subclass_enum.json --target-python-version 3.11 --use-specialized-enum # (1)!
    ```

    1. :material-arrow-left: `--use-specialized-enum` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "object",
      "properties": {
        "IntEnum": {
          "type": "integer",
          "enum": [
            1,
            2,
            3
          ]
        },
        "FloatEnum": {
          "type": "number",
          "enum": [
            1.1,
            2.1,
            3.1
          ]
        },
        "StrEnum": {
          "type": "string",
          "enum": [
            "1",
            "2",
            "3"
          ]
        },
        "NonTypedEnum": {
          "enum": [
            "1",
            "2",
            "3"
          ]
        },
        "BooleanEnum": {
          "type": "boolean",
          "enum": [
            true,
            false
          ]
        },
        "UnknownEnum": {
          "type": "unknown",
          "enum": [
            "a",
            "b"
          ]
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  subclass_enum.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum, IntEnum, StrEnum

    from pydantic import BaseModel, Field


    class IntEnumModel(IntEnum):
        integer_1 = 1
        integer_2 = 2
        integer_3 = 3


    class FloatEnum(Enum):
        number_1_1 = 1.1
        number_2_1 = 2.1
        number_3_1 = 3.1


    class StrEnumModel(StrEnum):
        field_1 = '1'
        field_2 = '2'
        field_3 = '3'


    class NonTypedEnum(Enum):
        field_1 = '1'
        field_2 = '2'
        field_3 = '3'


    class BooleanEnum(Enum):
        boolean_True = True
        boolean_False = False


    class UnknownEnum(Enum):
        a = 'a'
        b = 'b'


    class Model(BaseModel):
        IntEnum: IntEnumModel | None = None
        FloatEnum_1: FloatEnum | None = Field(None, alias='FloatEnum')
        StrEnum: StrEnumModel | None = None
        NonTypedEnum_1: NonTypedEnum | None = Field(None, alias='NonTypedEnum')
        BooleanEnum_1: BooleanEnum | None = Field(None, alias='BooleanEnum')
        UnknownEnum_1: UnknownEnum | None = Field(None, alias='UnknownEnum')
    ```

    <!-- fmt: on -->

---

## `--use-standard-collections` {#use-standard-collections}

Use built-in dict/list instead of typing.Dict/List.

The `--use-standard-collections` flag generates built-in container types
(dict, list) instead of typing module equivalents. This produces cleaner
code for Python 3.10+ where built-in types support subscripting.

**Related:** [`--use-generic-container-types`](#use-generic-container-types)

**See also:** [Python Version Compatibility](../python-version-compatibility.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input root_model_with_additional_properties.json --use-standard-collections # (1)!
    ```

    1. :material-arrow-left: `--use-standard-collections` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "$id": "test.json",
      "description": "test",
      "type": "object",
      "required": [
        "test_id",
        "test_ip",
        "result",
        "nested_object_result",
        "nested_enum_result"
      ],
      "properties": {
        "test_id": {
          "type": "string",
          "description": "test ID"
        },
        "test_ip": {
          "type": "string",
          "description": "test IP"
        },
        "result": {
          "type": "object",
          "additionalProperties": {
            "type": "integer"
          }
        },
        "nested_object_result": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "properties": {
              "status":{
                "type": "integer"
              }
            },
            "required": ["status"]
          }
        },
        "nested_enum_result": {
          "type": "object",
          "additionalProperties": {
            "enum": ["red", "green"]
          }
        },
        "all_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "allOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "one_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "oneOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "any_of_result" :{
          "type" : "object",
          "additionalProperties" :
          {
            "anyOf" : [
              { "$ref" : "#/definitions/User" },
              { "type" : "object",
                "properties": {
                  "description": {"type" : "string" }
                }
              }
            ]
          }
        },
        "all_of_with_unknown_object" :{
          "type" : "object",
          "additionalProperties" :
          {
            "allOf" : [
              { "$ref" : "#/definitions/User" },
              { "description": "TODO" }
            ]
          }
        },
        "objectRef": {
          "type": "object",
          "additionalProperties": {
            "$ref": "#/definitions/User"
          }
        },
        "deepNestedObjectRef": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "additionalProperties": {
              "type": "object",
              "additionalProperties": {
                 "$ref": "#/definitions/User"
              }
            }
          }
        }
      },
      "definitions": {
        "User": {
          "type": "object",
          "properties": {
            "name": {
              "type": "string"
            }
          }
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  root_model_with_additional_properties.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum

    from pydantic import BaseModel, Field


    class NestedObjectResult(BaseModel):
        status: int


    class NestedEnumResult(Enum):
        red = 'red'
        green = 'green'


    class OneOfResult(BaseModel):
        description: str | None = None


    class AnyOfResult(BaseModel):
        description: str | None = None


    class User(BaseModel):
        name: str | None = None


    class AllOfResult(User):
        description: str | None = None


    class Model(BaseModel):
        test_id: str = Field(..., description='test ID')
        test_ip: str = Field(..., description='test IP')
        result: dict[str, int]
        nested_object_result: dict[str, NestedObjectResult]
        nested_enum_result: dict[str, NestedEnumResult]
        all_of_result: dict[str, AllOfResult] | None = None
        one_of_result: dict[str, User | OneOfResult] | None = None
        any_of_result: dict[str, User | AnyOfResult] | None = None
        all_of_with_unknown_object: dict[str, User] | None = None
        objectRef: dict[str, User] | None = None
        deepNestedObjectRef: dict[str, dict[str, dict[str, User]]] | None = None
    ```

    <!-- fmt: on -->

---

## `--use-standard-primitive-types` {#use-standard-primitive-types}

Use Python standard library types for string formats instead of str.

The `--use-standard-primitive-types` flag configures the code generation to use
Python standard library types (UUID, IPv4Address, IPv6Address, Path) for corresponding
string formats instead of plain str. This affects dataclass, msgspec, and TypedDict
output types. Pydantic already uses these types by default.

**Related:** [`--output-datetime-class`](#output-datetime-class), [`--output-model-type`](model-customization.md#output-model-type)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input use_standard_primitive_types.json --output-model-type dataclasses.dataclass --use-standard-primitive-types # (1)!
    ```

    1. :material-arrow-left: `--use-standard-primitive-types` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "format": "uuid"
        },
        "ip_address": {
          "type": "string",
          "format": "ipv4"
        },
        "config_path": {
          "type": "string",
          "format": "path"
        }
      }
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  use_standard_primitive_types.json

    from __future__ import annotations

    from dataclasses import dataclass
    from ipaddress import IPv4Address
    from pathlib import Path
    from uuid import UUID


    @dataclass
    class Model:
        id: UUID | None = None
        ip_address: IPv4Address | None = None
        config_path: Path | None = None
    ```

    <!-- fmt: on -->

---

## `--use-strict-str-for-base64` {#use-strict-str-for-base64}

Make a `format: byte` string strict under `--strict-types str`.

`--strict-types str` writes a string as `StrictStr`, which refuses bytes, but leaves a `format: byte` string
`Base64Str`, or the `str` that `--use-encoded-str-for-constrained-base64` writes, which accept bytes:
`Model(b=b"aGk=")` gives `'hi'`. With this option the string is the `StrictStr` that `EncodedStr` decodes,
`Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)]`, with the constraints the field form writes in front of the
decoding, at a property, an item, a map value, a root, a union member and a collapsed root, so Pydantic 2.10 or later
refuses bytes for it as for any other strict string, while JSON input validates as before. Pydantic before 2.10
accepts bytes for it, and a target Pydantic older than 2.10 warns so. Without `--strict-types str` the option
changes nothing.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input encoded_str_for_constrained_base64.json --strict-types str --use-strict-str-for-base64 # (1)!
    ```

    1. :material-arrow-left: `--use-strict-str-for-base64` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "EncodedStrForConstrainedBase64",
      "type": "object",
      "required": ["required"],
      "properties": {
        "pattern": {"type": "string", "format": "byte", "pattern": "^Y"},
        "lengths": {"type": "string", "format": "byte", "minLength": 4, "maxLength": 8},
        "required": {"type": "string", "format": "byte", "minLength": 4},
        "nullable": {"type": ["string", "null"], "format": "byte", "maxLength": 8},
        "plain": {"type": "string", "format": "byte"},
        "items": {"type": "array", "items": {"type": "string", "format": "byte", "maxLength": 8}},
        "values": {"type": "object", "additionalProperties": {"type": "string", "format": "byte", "pattern": "^Y"}},
        "ref": {"$ref": "#/$defs/Short"},
        "member": {"anyOf": [{"type": "string", "format": "byte", "maxLength": 8}, {"type": "integer"}]},
        "branch": {"type": ["array", "string"], "format": "byte", "maxLength": 8, "items": {"type": "integer"}}
      },
      "$defs": {
        "Short": {"type": "string", "format": "byte", "maxLength": 8}
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  encoded_str_for_constrained_base64.json

        from __future__ import annotations

        from typing import Annotated

        from pydantic import Base64Encoder, BaseModel, EncodedStr, Field, RootModel, StrictStr
        from typing_extensions import TypeAliasType

        BranchString = TypeAliasType(
            "BranchString",
            Annotated[
                Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)], Field(max_length=8)
            ],
        )


        class Short(RootModel[Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)]]):
            root: Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)]


        class EncodedStrForConstrainedBase64(BaseModel):
            pattern: Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)] | None = None
            lengths: Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)] | None = None
            required: Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)]
            nullable: Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)] | None = None
            plain: Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)] | None = None
            items: list[Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)]] | None = None
            values: (
                dict[str, Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)]] | None
            ) = None
            ref: Short | None = None
            member: Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)] | int | None = None
            branch: BranchString | list[int] | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        > **Error:** File not found: main/jsonschema/encoded_str_for_constrained_base64~main_encoded_str_for_constrained_base64[strict_released].py

---

## `--use-total-false-for-typed-dict` {#use-total-false-for-typed-dict}

Generate TypedDict declarations with `total=False`.

Optional fields are left unwrapped, while required fields are marked with
`Required[...]`. This can substantially reduce generated code when most fields
are optional.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input use_total_false_for_typed_dict.json --output-model-type typing.TypedDict --use-total-false-for-typed-dict --use-frozen-field # (1)!
    ```

    1. :material-arrow-left: `--use-total-false-for-typed-dict` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "$defs": {
        "ClassModel": {
          "type": "object",
          "properties": {
            "required_value": {
              "type": "string"
            },
            "optional_value": {
              "type": "integer"
            },
            "required_read_only": {
              "type": "boolean",
              "readOnly": true
            },
            "optional_read_only": {
              "type": "number",
              "readOnly": true
            }
          },
          "required": [
            "required_value",
            "required_read_only"
          ]
        },
        "BaseModel": {
          "type": "object",
          "properties": {
            "base_required": {
              "type": "string"
            },
            "base_optional": {
              "type": "integer"
            }
          },
          "required": [
            "base_required"
          ]
        },
        "FunctionalModel": {
          "allOf": [
            {
              "$ref": "#/$defs/BaseModel"
            },
            {
              "type": "object",
              "properties": {
                "invalid-key": {
                  "type": "string"
                },
                "child_optional": {
                  "type": "boolean"
                }
              },
              "required": [
                "invalid-key"
              ]
            }
          ]
        },
        "ClosedModel": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "closed_required": {
              "type": "string"
            },
            "closed_optional": {
              "type": "integer"
            }
          },
          "required": [
            "closed_required"
          ]
        },
        "ExtraItemsModel": {
          "type": "object",
          "additionalProperties": {
            "type": "integer"
          },
          "properties": {
            "extra_required": {
              "type": "string"
            },
            "extra_optional": {
              "type": "boolean"
            }
          },
          "required": [
            "extra_required"
          ]
        }
      },
      "type": "object",
      "properties": {
        "class_model": {
          "$ref": "#/$defs/ClassModel"
        },
        "functional_model": {
          "$ref": "#/$defs/FunctionalModel"
        },
        "closed_model": {
          "$ref": "#/$defs/ClosedModel"
        },
        "extra_items_model": {
          "$ref": "#/$defs/ExtraItemsModel"
        }
      },
      "required": [
        "class_model"
      ]
    }
    ```

    **Output:**

    === "Python 3.10"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  use_total_false_for_typed_dict.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing_extensions import ReadOnly, Required, TypedDict


        class ClassModel(TypedDict, total=False):
            required_value: Required[str]
            optional_value: int
            required_read_only: Required[ReadOnly[bool]]
            optional_read_only: ReadOnly[float]


        class BaseModel(TypedDict, total=False):
            base_required: Required[str]
            base_optional: int


        FunctionalModel = TypedDict(
            'FunctionalModel',
            {
                'base_required': Required[str],
                'base_optional': int,
                'invalid-key': Required[str],
                'child_optional': bool,
            },
            total=False,
        )


        class ClosedModel(TypedDict, total=False, closed=True):
            closed_required: Required[str]
            closed_optional: int


        class ExtraItemsModel(TypedDict, total=False, extra_items=int):
            extra_required: Required[str]
            extra_optional: bool


        class Model(TypedDict, total=False):
            class_model: Required[ClassModel]
            functional_model: FunctionalModel
            closed_model: ClosedModel
            extra_items_model: ExtraItemsModel
        ```

        <!-- fmt: on -->

    === "Python 3.11"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  use_total_false_for_typed_dict.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Required

        from typing_extensions import ReadOnly, TypedDict


        class ClassModel(TypedDict, total=False):
            required_value: Required[str]
            optional_value: int
            required_read_only: Required[ReadOnly[bool]]
            optional_read_only: ReadOnly[float]


        class BaseModel(TypedDict, total=False):
            base_required: Required[str]
            base_optional: int


        FunctionalModel = TypedDict(
            'FunctionalModel',
            {
                'base_required': Required[str],
                'base_optional': int,
                'invalid-key': Required[str],
                'child_optional': bool,
            },
            total=False,
        )


        class ClosedModel(TypedDict, total=False, closed=True):
            closed_required: Required[str]
            closed_optional: int


        class ExtraItemsModel(TypedDict, total=False, extra_items=int):
            extra_required: Required[str]
            extra_optional: bool


        class Model(TypedDict, total=False):
            class_model: Required[ClassModel]
            functional_model: FunctionalModel
            closed_model: ClosedModel
            extra_items_model: ExtraItemsModel
        ```

        <!-- fmt: on -->

---

## `--use-tuple-for-closed-arrays` {#use-tuple-for-closed-arrays}

Generate tuple types for positional arrays that no other item can follow.

A `prefixItems` array whose tail is closed by `items: false`, `unevaluatedItems: false`, a `false` position or
`contains`, and an `items` array closed by `additionalItems: false` or a `false` position, is written as a tuple
when its length bounds fix its length, so every position keeps its own type. Without the option only equal
`minItems` and `maxItems` make a tuple, and a closed array is a list with length constraints that accepts items in
the wrong positions. When bounds other than equal `minItems` and `maxItems` fix the length, the list stays if its
item schemas (the allowed positions and the `items` schema) accept the same values, compared without annotations
and `x-` keys, with a `$ref` read as its target and `type: [T]` as `type: T`. `--use-tuple-for-fixed-items` still
decides whether the `items` array form is read. A tuple cannot hold `uniqueItems`: with `--use-unique-items-as-set`
it stays a tuple and a warning names the field generated without uniqueness, unless `--generate-schema-validators`
checks it.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input prefix_items_closed_length.json --use-tuple-for-closed-arrays # (1)!
    ```

    1. :material-arrow-left: `--use-tuple-for-closed-arrays` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "PrefixItemsClosedLength",
      "type": "object",
      "properties": {
        "closed_by_items_false": {
          "type": "array",
          "prefixItems": [{"type": "string"}, {"type": "integer"}],
          "items": false,
          "minItems": 2
        },
        "closed_by_unevaluated_items_false": {
          "type": "array",
          "prefixItems": [{"type": "string"}, {"type": "integer"}],
          "unevaluatedItems": false,
          "minItems": 2
        },
        "closed_by_false_position": {
          "type": "array",
          "prefixItems": [{"type": "string"}, {"type": "integer"}, false],
          "minItems": 2
        },
        "closed_below_max_items": {
          "type": "array",
          "prefixItems": [{"type": "string"}, {"type": "integer"}],
          "items": false,
          "minItems": 2,
          "maxItems": 5
        },
        "closed_nullable": {
          "type": ["array", "null"],
          "prefixItems": [{"type": "string"}, {"type": "integer"}],
          "items": false,
          "minItems": 2
        },
        "closed_by_contains": {
          "type": "array",
          "prefixItems": [{"type": "string"}, {"type": "integer"}],
          "contains": true,
          "minContains": 2,
          "maxContains": 2
        },
        "closed_by_contains_beyond_positions": {
          "type": "array",
          "prefixItems": [{"type": "string"}],
          "contains": {},
          "minContains": 3,
          "maxContains": 3
        },
        "closed_to_one_position": {
          "type": "array",
          "prefixItems": [{"type": "string"}, {"type": "integer"}],
          "contains": {},
          "maxContains": 1
        },
        "closed_before_other_positions": {
          "type": "array",
          "prefixItems": [{"type": "string"}, {"type": "string"}, {"type": "integer"}],
          "items": false,
          "maxItems": 2,
          "contains": {},
          "minContains": 2
        },
        "kept_one_schema": {
          "type": "array",
          "prefixItems": [{"type": "string"}, {"type": "string"}],
          "items": false,
          "minItems": 2
        },
        "kept_one_schema_apart_from_annotations": {
          "type": "array",
          "prefixItems": [{"type": "string", "description": "first", "$comment": "first position"}, {"type": "string"}],
          "items": false,
          "minItems": 2
        },
        "kept_one_position": {
          "type": "array",
          "prefixItems": [{"type": "string"}],
          "items": false,
          "minItems": 1
        },
        "kept_one_schema_with_tail": {
          "type": "array",
          "prefixItems": [{"type": "string", "x-position": "first"}],
          "items": {"type": "string", "x-position": "first"},
          "contains": {},
          "minContains": 3,
          "maxContains": 3
        },
        "kept_variable_length": {
          "type": "array",
          "prefixItems": [{"type": "string"}, {"type": "integer"}],
          "items": false,
          "minItems": 1
        },
        "closed_by_type_list": {
          "type": "array",
          "prefixItems": [{"type": ["string", "null"]}, {"type": "string"}],
          "items": false,
          "minItems": 2
        },
        "kept_apart_from_extension_keys": {
          "type": "array",
          "prefixItems": [{"type": "string", "x-order": 1}, {"type": "string"}],
          "items": false,
          "minItems": 2
        },
        "kept_ref_and_equal_inline": {
          "type": "array",
          "prefixItems": [{"$ref": "#/$defs/Name"}, {"type": "string", "maxLength": 5}],
          "items": false,
          "minItems": 2
        },
        "kept_type_list_of_one": {
          "type": "array",
          "prefixItems": [{"type": ["string"]}, {"type": "string"}],
          "items": false,
          "minItems": 2
        },
        "kept_ref_to_root_with_extension_key": {
          "type": "array",
          "prefixItems": [{"$ref": "#/$defs/Tagged"}, {"type": "integer"}],
          "items": false,
          "minItems": 2
        },
        "kept_nested_items": {
          "type": "array",
          "prefixItems": [
            {"type": "array", "items": {"type": "string", "title": "Tag"}},
            {"type": "array", "items": {"type": "string"}}
          ],
          "items": false,
          "minItems": 2
        },
        "kept_nested_any_of": {
          "type": "array",
          "prefixItems": [
            {"anyOf": [{"type": "string"}, {"type": "integer", "description": "A count."}]},
            {"anyOf": [{"type": "string"}, {"type": "integer"}]}
          ],
          "items": false,
          "minItems": 2
        },
        "kept_nested_dependent_schemas": {
          "type": "array",
          "prefixItems": [
            {"type": "object", "dependentSchemas": {"a": {"type": "object", "required": ["b"], "description": "Needs b."}}},
            {"type": "object", "dependentSchemas": {"a": {"type": "object", "required": ["b"]}}}
          ],
          "items": false,
          "minItems": 2
        },
        "explicit_one_schema": {
          "type": "array",
          "prefixItems": [{"type": "string"}, {"type": "string"}],
          "minItems": 2,
          "maxItems": 2
        },
        "inherited": {"$ref": "#/$defs/Child"}
      },
      "$defs": {
        "Name": {"type": "string", "maxLength": 5, "description": "A name."},
        "Tagged": {"type": "integer", "x-kind": "tag"},
        "Parent": {
          "type": "object",
          "properties": {
            "closed": {
              "type": "array",
              "prefixItems": [{"type": "string"}, {"type": "integer"}],
              "items": false,
              "minItems": 2
            }
          }
        },
        "Child": {
          "allOf": [
            {"$ref": "#/$defs/Parent"},
            {"type": "object", "properties": {"closed": {"type": "array", "prefixItems": [{"type": "string", "maxLength": 3}]}}}
          ]
        }
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  prefix_items_closed_length.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Any

        from pydantic import BaseModel, Field, RootModel, constr


        class KeptRefAndEqualInlineItem(RootModel[constr(max_length=5)]):
            root: constr(max_length=5)


        class Name(RootModel[constr(max_length=5)]):
            root: constr(max_length=5) = Field(..., description='A name.')


        class Tagged(RootModel[int]):
            root: int


        class Parent(BaseModel):
            closed: tuple[str, int] | None = None


        class ClosedItem(RootModel[constr(max_length=3)]):
            root: constr(max_length=3)


        class Child(Parent):
            closed: tuple[ClosedItem, int] | None = None


        class PrefixItemsClosedLength(BaseModel):
            closed_by_items_false: tuple[str, int] | None = None
            closed_by_unevaluated_items_false: tuple[str, int] | None = None
            closed_by_false_position: tuple[str, int] | None = None
            closed_below_max_items: tuple[str, int] | None = None
            closed_nullable: tuple[str, int] | None = None
            closed_by_contains: tuple[str, int] | None = None
            closed_by_contains_beyond_positions: tuple[str, Any, Any] | None = None
            closed_to_one_position: tuple[str] | None = None
            closed_before_other_positions: tuple[str, str] | None = None
            kept_one_schema: list[str] | None = Field(None, max_length=2, min_length=2)
            kept_one_schema_apart_from_annotations: list[str] | None = Field(
                None, max_length=2, min_length=2
            )
            kept_one_position: list[str] | None = Field(None, max_length=1, min_length=1)
            kept_one_schema_with_tail: list[str] | None = Field(
                None, max_length=3, min_length=3
            )
            kept_variable_length: list[str | int] | None = Field(
                None, max_length=2, min_length=1
            )
            closed_by_type_list: tuple[str | None, str] | None = None
            kept_apart_from_extension_keys: list[str] | None = Field(
                None, max_length=2, min_length=2
            )
            kept_ref_and_equal_inline: list[Name | KeptRefAndEqualInlineItem] | None = Field(
                None, max_length=2, min_length=2
            )
            kept_type_list_of_one: list[str] | None = Field(None, max_length=2, min_length=2)
            kept_ref_to_root_with_extension_key: list[Tagged | int] | None = Field(
                None, max_length=2, min_length=2
            )
            kept_nested_items: list[list[str]] | None = Field(None, max_length=2, min_length=2)
            kept_nested_any_of: list[str | int] | None = Field(None, max_length=2, min_length=2)
            kept_nested_dependent_schemas: list[dict[str, Any]] | None = Field(
                None, max_length=2, min_length=2
            )
            explicit_one_schema: tuple[str, str] | None = None
            inherited: Child | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  prefix_items_closed_length.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Any

        from pydantic import BaseModel, Field, RootModel, constr


        class KeptRefAndEqualInlineItem(RootModel[constr(max_length=5)]):
            root: constr(max_length=5)


        class Name(RootModel[constr(max_length=5)]):
            root: constr(max_length=5) = Field(..., description='A name.')


        class Tagged(RootModel[int]):
            root: int


        class Parent(BaseModel):
            closed: list[str | int] | None = Field(None, max_length=2, min_length=2)


        class ClosedItem(RootModel[constr(max_length=3)]):
            root: constr(max_length=3)


        class Child(Parent):
            closed: list[ClosedItem | int] | None = Field(None, max_length=2, min_length=2)


        class PrefixItemsClosedLength(BaseModel):
            closed_by_items_false: list[str | int] | None = Field(
                None, max_length=2, min_length=2
            )
            closed_by_unevaluated_items_false: list[str | int] | None = Field(
                None, max_length=2, min_length=2
            )
            closed_by_false_position: list[str | int] | None = Field(
                None, max_length=2, min_length=2
            )
            closed_below_max_items: list[str | int] | None = Field(
                None, max_length=2, min_length=2
            )
            closed_nullable: list[str | int] | None = Field(None, max_length=2, min_length=2)
            closed_by_contains: list[Any] | None = Field(None, max_length=2, min_length=2)
            closed_by_contains_beyond_positions: list[Any] | None = Field(
                None, max_length=3, min_length=3
            )
            closed_to_one_position: list[Any] | None = Field(None, max_length=1, min_length=1)
            closed_before_other_positions: list[str | int] | None = Field(
                None, max_length=2, min_length=2
            )
            kept_one_schema: list[str] | None = Field(None, max_length=2, min_length=2)
            kept_one_schema_apart_from_annotations: list[str] | None = Field(
                None, max_length=2, min_length=2
            )
            kept_one_position: list[str] | None = Field(None, max_length=1, min_length=1)
            kept_one_schema_with_tail: list[str] | None = Field(
                None, max_length=3, min_length=3
            )
            kept_variable_length: list[str | int] | None = Field(
                None, max_length=2, min_length=1
            )
            closed_by_type_list: list[str | None] | None = Field(
                None, max_length=2, min_length=2
            )
            kept_apart_from_extension_keys: list[str] | None = Field(
                None, max_length=2, min_length=2
            )
            kept_ref_and_equal_inline: list[Name | KeptRefAndEqualInlineItem] | None = Field(
                None, max_length=2, min_length=2
            )
            kept_type_list_of_one: list[str] | None = Field(None, max_length=2, min_length=2)
            kept_ref_to_root_with_extension_key: list[Tagged | int] | None = Field(
                None, max_length=2, min_length=2
            )
            kept_nested_items: list[list[str]] | None = Field(None, max_length=2, min_length=2)
            kept_nested_any_of: list[str | int] | None = Field(None, max_length=2, min_length=2)
            kept_nested_dependent_schemas: list[dict[str, Any]] | None = Field(
                None, max_length=2, min_length=2
            )
            explicit_one_schema: tuple[str, str] | None = None
            inherited: Child | None = None
        ```

        <!-- fmt: on -->

---

## `--use-tuple-for-fixed-items` {#use-tuple-for-fixed-items}

Generate tuple types for arrays with items array syntax.

When `--use-tuple-for-fixed-items` is enabled and an array has `items` as an array
with `minItems == maxItems == len(items)`, generate a tuple type instead of a list.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input items_array_tuple.json --use-tuple-for-fixed-items # (1)!
    ```

    1. :material-arrow-left: `--use-tuple-for-fixed-items` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft-07/schema",
      "type": "object",
      "properties": {
        "point": {
          "type": "array",
          "items": [{"type": "number"}, {"type": "number"}],
          "minItems": 2,
          "maxItems": 2
        }
      },
      "required": ["point"]
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  items_array_tuple.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Model(BaseModel):
        point: tuple[float, float]
    ```

    <!-- fmt: on -->

---

## `--use-tuple-for-fixed-length-arrays` {#use-tuple-for-fixed-length-arrays}

Generate tuple types for homogeneous fixed-length arrays.

When `--use-tuple-for-fixed-length-arrays` is enabled and an array has one
`items` schema with `minItems == maxItems`, generate a tuple type instead of a
list. An empty fixed-length array becomes `tuple[()]`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input fixed_length_array_tuples.yaml --use-tuple-for-fixed-length-arrays --output-model-type pydantic_v2.BaseModel # (1)!
    ```

    1. :material-arrow-left: `--use-tuple-for-fixed-length-arrays` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.0.3
    info:
      title: Fixed length array tuples
      version: 1.0.0
    paths: {}
    components:
      schemas:
        FixedLengthArrayTuples:
          type: object
          required:
            - vector
            - allOfVector
            - allOfEmpty
            - empty
            - inlineObject
            - refValues
            - range
            - minimumOnly
          properties:
            vector:
              type: array
              minItems: 3
              maxItems: 3
              items:
                type: number
                format: double
            allOfVector:
              allOf:
                - type: array
                  minItems: 2
                  maxItems: 2
                  items:
                    type: string
            allOfEmpty:
              allOf:
                - type: array
                  minItems: 0
                  maxItems: 0
                  items:
                    type: string
            empty:
              type: array
              minItems: 0
              maxItems: 0
              items:
                type: number
                format: double
            inlineObject:
              type: array
              minItems: 2
              maxItems: 2
              items:
                type: object
                required:
                  - value
                properties:
                  value:
                    type: string
            refValues:
              type: array
              minItems: 3
              maxItems: 3
              items:
                $ref: '#/components/schemas/RefValue'
            range:
              type: array
              minItems: 2
              maxItems: 4
              items:
                type: integer
            minimumOnly:
              type: array
              minItems: 3
              items:
                type: string
        RefValue:
          type: object
          required:
            - id
          properties:
            id:
              type: integer
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  fixed_length_array_tuples.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, Field, RootModel


    class AllOfVector(RootModel[tuple[str, str]]):
        root: tuple[str, str]


    class AllOfEmpty(RootModel[tuple[()]]):
        root: tuple[()]


    class InlineObjectItem(BaseModel):
        value: str


    class RefValue(BaseModel):
        id: int


    class FixedLengthArrayTuples(BaseModel):
        vector: tuple[float, float, float]
        allOfVector: AllOfVector
        allOfEmpty: AllOfEmpty
        empty: tuple[()]
        inlineObject: tuple[InlineObjectItem, InlineObjectItem]
        refValues: tuple[RefValue, RefValue, RefValue]
        range: list[int] = Field(..., max_length=4, min_length=2)
        minimumOnly: list[str] = Field(..., min_length=3)
    ```

    <!-- fmt: on -->

---

## `--use-type-alias` {#use-type-alias}

Use TypeAlias instead of root models for type definitions (experimental).

The `--use-type-alias` flag generates TypeAlias declarations instead of
root model classes for certain type definitions. For Python 3.10-3.11, it
generates TypeAliasType, and for Python 3.12+, it uses the 'type' statement
syntax. This feature is experimental.

**Related:** [`--target-python-version`](model-customization.md#target-python-version)

**See also:** [Model Reuse and Deduplication](../model-reuse.md), [Root Models and Type Aliases](../root-model-and-type-alias.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input type_alias.graphql --use-type-alias # (1)!
    ```

    1. :material-arrow-left: `--use-type-alias` - the option documented here

??? example "Examples"

    === "JSON Schema"

        **Input Schema:**

        ```json
        {
          "$schema": "http://json-schema.org/draft-07/schema#",
          "definitions": {
            "SimpleString": {
              "type": "string"
            },
            "UnionType": {
              "anyOf": [
                {"type": "string"},
                {"type": "integer"}
              ]
            },
            "ArrayType": {
              "type": "array",
              "items": {"type": "string"}
            },
            "AnnotatedType": {
              "title": "MyAnnotatedType",
              "description": "An annotated union type",
              "anyOf": [
                {"type": "string"},
                {"type": "boolean"}
              ]
            },
            "ModelWithTypeAliasField": {
              "type": "object",
              "properties": {
                "simple_field": {"$ref": "#/definitions/SimpleString"},
                "union_field": {"$ref": "#/definitions/UnionType"},
                "array_field": {"$ref": "#/definitions/ArrayType"},
                "annotated_field": {"$ref": "#/definitions/AnnotatedType"}
              }
            }
          }
        }
        ```

        **Output:**

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  type_alias.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Annotated, Any

        from pydantic import BaseModel, Field
        from typing_extensions import TypeAliasType

        Model = TypeAliasType("Model", Any)


        SimpleString = TypeAliasType("SimpleString", str)


        UnionType = TypeAliasType("UnionType", str | int)


        ArrayType = TypeAliasType("ArrayType", list[str])


        AnnotatedType = TypeAliasType(
            "AnnotatedType",
            Annotated[
                str | bool,
                Field(..., description='An annotated union type', title='MyAnnotatedType'),
            ],
        )


        class ModelWithTypeAliasField(BaseModel):
            simple_field: SimpleString | None = None
            union_field: UnionType | None = None
            array_field: ArrayType | None = None
            annotated_field: AnnotatedType | None = None
        ```

        <!-- fmt: on -->

    === "GraphQL"

        **Input Schema:**

        ```graphql
        scalar SimpleString
        
        type Person {
          name: String!
          age: Int!
        }
        
        type Pet {
          name: String!
          type: String!
        }
        
        union UnionType = Person | Pet
        
        type ModelWithTypeAliasField {
          simple_field: SimpleString
          union_field: UnionType
          string_field: String
        }
        ```

        **Output:**

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  type_alias.graphql
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Literal, Union

        from pydantic import BaseModel, Field
        from typing_extensions import TypeAliasType

        Boolean = TypeAliasType("Boolean", bool)
        """
        The `Boolean` scalar type represents `true` or `false`.
        """


        Int = TypeAliasType("Int", int)
        """
        The `Int` scalar type represents non-fractional signed whole numeric values. Int can represent values between -(2^31) and 2^31 - 1.
        """


        SimpleString = TypeAliasType("SimpleString", str)


        String = TypeAliasType("String", str)
        """
        The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
        """


        class Person(BaseModel):
            age: Int
            name: String
            typename__: Literal['Person'] | None = Field('Person', alias='__typename')

            @property
            def __typename(self) -> Literal['Person'] | None:
                return self.typename__


        class Pet(BaseModel):
            name: String
            type: String
            typename__: Literal['Pet'] | None = Field('Pet', alias='__typename')

            @property
            def __typename(self) -> Literal['Pet'] | None:
                return self.typename__


        UnionType = TypeAliasType(
            "UnionType",
            Union[
                'Person',
                'Pet',
            ],
        )


        class ModelWithTypeAliasField(BaseModel):
            simple_field: SimpleString | None = None
            string_field: String | None = None
            union_field: UnionType | None = None
            typename__: Literal['ModelWithTypeAliasField'] | None = Field(
                'ModelWithTypeAliasField', alias='__typename'
            )

            @property
            def __typename(self) -> Literal['ModelWithTypeAliasField'] | None:
                return self.typename__
        ```

        <!-- fmt: on -->

---

## `--use-type-alias-type` {#use-type-alias-type}

Use runtime TypeAliasType objects for aliases before Python 3.12 (experimental).

The `--use-type-alias-type` flag implies `--use-type-alias` and forces
`TypeAliasType` for every output model type on Python 3.10-3.11. Python 3.12+
continues to use native `type` statements.

**See also:** [Root Models and Type Aliases](../root-model-and-type-alias.md)

**Option relationships:**

- **Implies:** [`--use-type-alias`](typing-customization.md#use-type-alias) enabled

!!! tip "Usage"

    ```bash
    datamodel-codegen --input type_alias.json --output-model-type typing.TypedDict --use-type-alias-type --target-python-version 3.10 # (1)!
    ```

    1. :material-arrow-left: `--use-type-alias-type` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "definitions": {
        "SimpleString": {
          "type": "string"
        },
        "UnionType": {
          "anyOf": [
            {"type": "string"},
            {"type": "integer"}
          ]
        },
        "ArrayType": {
          "type": "array",
          "items": {"type": "string"}
        },
        "AnnotatedType": {
          "title": "MyAnnotatedType",
          "description": "An annotated union type",
          "anyOf": [
            {"type": "string"},
            {"type": "boolean"}
          ]
        },
        "ModelWithTypeAliasField": {
          "type": "object",
          "properties": {
            "simple_field": {"$ref": "#/definitions/SimpleString"},
            "union_field": {"$ref": "#/definitions/UnionType"},
            "array_field": {"$ref": "#/definitions/ArrayType"},
            "annotated_field": {"$ref": "#/definitions/AnnotatedType"}
          }
        }
      }
    }
    ```

    **Output:**

    === "Python 3.10"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  type_alias.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Any, TypedDict

        from typing_extensions import NotRequired, TypeAliasType

        Model = TypeAliasType("Model", Any)


        SimpleString = TypeAliasType("SimpleString", str)


        UnionType = TypeAliasType("UnionType", str | int)


        ArrayType = TypeAliasType("ArrayType", list[str])


        AnnotatedType = TypeAliasType("AnnotatedType", str | bool)


        class ModelWithTypeAliasField(TypedDict):
            simple_field: NotRequired[SimpleString]
            union_field: NotRequired[UnionType]
            array_field: NotRequired[ArrayType]
            annotated_field: NotRequired[AnnotatedType]
        ```

        <!-- fmt: on -->

    === "Python 3.12"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  type_alias.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Any, NotRequired, TypedDict

        type Model = Any


        type SimpleString = str


        type UnionType = str | int


        type ArrayType = list[str]


        type AnnotatedType = str | bool


        class ModelWithTypeAliasField(TypedDict):
            simple_field: NotRequired[SimpleString]
            union_field: NotRequired[UnionType]
            array_field: NotRequired[ArrayType]
            annotated_field: NotRequired[AnnotatedType]
        ```

        <!-- fmt: on -->

---

## `--use-typed-map-values-beside-other-types` {#use-typed-map-values-beside-other-types}

Type the map values of type arrays that list object beside other types.

With `--use-typed-nullable-map-values`, a schema whose type array lists `object` beside other types, such as
`type: [object, string]` or `type: [object, string, "null"]`, and that declares its values with
`additionalProperties` gets typed values too, such as `dict[str, int] | str`, with a model for object values.
Without this option those maps keep `dict[str, Any]`, as in 0.86.0, while the maps whose type array lists `object`
alone or with `null` are typed by `--use-typed-nullable-map-values` either way. The option changes nothing without
`--use-typed-nullable-map-values`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input typed_map_values_beside_other_types.json --output-model-type pydantic_v2.BaseModel --use-typed-nullable-map-values --use-typed-map-values-beside-other-types # (1)!
    ```

    1. :material-arrow-left: `--use-typed-map-values-beside-other-types` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "Settings",
      "type": "object",
      "properties": {
        "limits": {
          "type": ["object", "null"],
          "additionalProperties": {"type": "integer"}
        },
        "quotas": {
          "type": ["object", "string"],
          "additionalProperties": {"type": "integer"}
        },
        "owners": {
          "type": ["object", "string", "null"],
          "additionalProperties": {
            "type": "object",
            "properties": {"name": {"type": "string"}}
          }
        }
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  typed_map_values_beside_other_types.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel


        class Owners(BaseModel):
            name: str | None = None


        class Settings(BaseModel):
            limits: dict[str, int] | None = None
            quotas: dict[str, int] | str | None = None
            owners: dict[str, Owners] | str | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  typed_map_values_beside_other_types.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Any

        from pydantic import BaseModel


        class Settings(BaseModel):
            limits: dict[str, int] | None = None
            quotas: dict[str, Any] | str | None = None
            owners: dict[str, Any] | str | None = None
        ```

        <!-- fmt: on -->

---

## `--use-typed-nullable-map-values` {#use-typed-nullable-map-values}

Type the values of nullable maps as their schemas say instead of Any.

A nullable map here is a schema whose type array lists `object` alone or with `null`, such as
`type: [object, "null"]` or `type: [object]`, and that declares its values with `additionalProperties`. Without the
`--use-typed-nullable-map-values` flag the output keeps the 0.83.0 form, `dict[str, Any] | None`, or
`dict[str, Any]` when the type array has no `null`, whatever the values are. A map made nullable by `nullable: true`
and a map typed `type: object` keep their typed values either way. The maps of type arrays that list `object`
beside other types, such as `type: [object, string]`, are typed only with
`--use-typed-map-values-beside-other-types` too.

With the flag the values follow their schema, such as `dict[str, int] | None` or `dict[str, Player] | None`, and an
object value gets a model named after the map, such as `Teams1` for the values of `Teams`. Those value models take
the names left free, so the other models keep their 0.83.0 names, and options keyed by class name, such as
`--base-class-map` or `--aliases`, apply to the final names. Typed values change more than the maps themselves:
`--reuse-model` makes a value model and the models equal to it one model, which can leave out a schema's class, and
no longer makes nullable maps with different values subclasses of each other, and `--collapse-root-models` collapses
the root models that the values use. When typing the values would change the modules written, the values of the
whole run stay untyped.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input nullable_map_value_types.yaml --use-typed-nullable-map-values --output-model-type pydantic_v2.BaseModel # (1)!
    ```

    1. :material-arrow-left: `--use-typed-nullable-map-values` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Nullable map value types
      version: "1.0"
    paths:
      /scores:
        get:
          operationId: listScores
          parameters:
            - name: players
              in: query
              schema:
                type: [object, "null"]
                additionalProperties:
                  $ref: "#/components/schemas/Player"
          responses:
            "200":
              description: The scores.
        post:
          operationId: replaceScores
          requestBody:
            content:
              application/json:
                schema:
                  type: [object, "null"]
                  additionalProperties:
                    type: integer
          responses:
            "200":
              description: The stored scores.
              content:
                application/json:
                  schema:
                    $ref: "#/components/schemas/Scores"
    components:
      schemas:
        Player:
          type: object
          properties:
            name:
              type: string
        Scores:
          type: [object, "null"]
          additionalProperties:
            type: integer
        NullFirstScores:
          type: ["null", object]
          additionalProperties:
            type: integer
        ObjectOnlyScores:
          type: [object]
          additionalProperties:
            type: integer
        Players:
          type: [object, "null"]
          additionalProperties:
            $ref: "#/components/schemas/Player"
        Teams:
          type: [object, "null"]
          additionalProperties:
            type: object
            properties:
              captain:
                $ref: "#/components/schemas/Player"
        Rounds:
          type: [object, "null"]
          additionalProperties:
            type: array
            items:
              type: integer
        Seasons:
          type: [object, "null"]
          additionalProperties:
            type: [object, "null"]
            additionalProperties:
              type: number
        Labels:
          type: [object, "null"]
          additionalProperties:
            type: string
            minLength: 1
        Board:
          type: object
          required:
            - scores
          properties:
            scores:
              type: [object, "null"]
              additionalProperties:
                type: integer
            players:
              type: [object, "null"]
              additionalProperties:
                $ref: "#/components/schemas/Player"
            teams:
              type: [object, "null"]
              additionalProperties:
                type: object
                properties:
                  captain:
                    $ref: "#/components/schemas/Player"
            history:
              type: array
              items:
                type: [object, "null"]
                additionalProperties:
                  type: integer
            plain:
              type: object
              additionalProperties:
                type: integer
            nullable_array:
              type: [array, "null"]
              items:
                type: integer
            untyped:
              type: [object, "null"]
              additionalProperties: true
            fixed:
              type: [object, "null"]
              additionalProperties:
                type: integer
              const:
                x: 1
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  nullable_map_value_types.yaml

        from __future__ import annotations

        from typing import Any

        from pydantic import BaseModel, RootModel, constr


        class Player(BaseModel):
            name: str | None = None


        class Scores(RootModel[dict[str, int] | None]):
            root: dict[str, int] | None


        class NullFirstScores(RootModel[dict[str, int] | None]):
            root: dict[str, int] | None


        class ObjectOnlyScores(RootModel[dict[str, int]]):
            root: dict[str, int]


        class Players(RootModel[dict[str, Player] | None]):
            root: dict[str, Player] | None


        class Teams1(BaseModel):
            captain: Player | None = None


        class Teams(RootModel[dict[str, Teams1] | None]):
            root: dict[str, Teams1] | None


        class Rounds(RootModel[dict[str, list[int]] | None]):
            root: dict[str, list[int]] | None


        class Seasons(RootModel[dict[str, dict[str, float] | None] | None]):
            root: dict[str, dict[str, float] | None] | None


        class Labels(RootModel[dict[str, constr(min_length=1)] | None]):
            root: dict[str, constr(min_length=1)] | None


        class Teams2(BaseModel):
            captain: Player | None = None


        class Board(BaseModel):
            scores: dict[str, int] | None
            players: dict[str, Player] | None = None
            teams: dict[str, Teams2] | None = None
            history: list[dict[str, int] | None] | None = None
            plain: dict[str, int] | None = None
            nullable_array: list[int] | None = None
            untyped: dict[str, Any] | None = None
            fixed: dict[str, int] = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  nullable_map_value_types.yaml

        from __future__ import annotations

        from typing import Any

        from pydantic import BaseModel, RootModel


        class Player(BaseModel):
            name: str | None = None


        class Scores(RootModel[dict[str, Any] | None]):
            root: dict[str, Any] | None


        class NullFirstScores(RootModel[dict[str, Any] | None]):
            root: dict[str, Any] | None


        class ObjectOnlyScores(RootModel[dict[str, Any]]):
            root: dict[str, Any]


        class Players(RootModel[dict[str, Any] | None]):
            root: dict[str, Any] | None


        class Teams(RootModel[dict[str, Any] | None]):
            root: dict[str, Any] | None


        class Rounds(RootModel[dict[str, Any] | None]):
            root: dict[str, Any] | None


        class Seasons(RootModel[dict[str, Any] | None]):
            root: dict[str, Any] | None


        class Labels(RootModel[dict[str, Any] | None]):
            root: dict[str, Any] | None


        class Board(BaseModel):
            scores: dict[str, Any] | None
            players: dict[str, Any] | None = None
            teams: dict[str, Any] | None = None
            history: list[dict[str, Any] | None] | None = None
            plain: dict[str, int] | None = None
            nullable_array: list[int] | None = None
            untyped: dict[str, Any] | None = None
            fixed: dict[str, int] = None
        ```

        <!-- fmt: on -->

---

## `--use-union-operator` {#use-union-operator}

Use | operator for Union types (PEP 604).

The `--use-union-operator` flag generates union types using the | operator
(e.g., `str | None`) instead of `Union[str, None]` or `Optional[str]`.
This is the default behavior.

**Related:** [`--no-use-union-operator`](#no-use-union-operator)

**See also:** [Python Version Compatibility](../python-version-compatibility.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input annotated.graphql --output-model-type pydantic_v2.BaseModel --use-annotated --use-union-operator # (1)!
    ```

    1. :material-arrow-left: `--use-union-operator` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```graphql
    type A {
        field: String!
        optionalField: String
        listField: [String!]!
        listOptionalField: [String]!
        optionalListField: [String!]
        optionalListOptionalField: [String]
        listListField:[[String!]!]!
    }
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  annotated.graphql
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Annotated, Literal

    from pydantic import BaseModel, Field
    from typing_extensions import TypeAliasType

    Boolean = TypeAliasType("Boolean", bool)
    """
    The `Boolean` scalar type represents `true` or `false`.
    """


    String = TypeAliasType("String", str)
    """
    The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
    """


    class A(BaseModel):
        field: String
        listField: list[String]
        listListField: list[list[String]]
        listOptionalField: list[String | None]
        optionalField: String | None = None
        optionalListField: list[String] | None = None
        optionalListOptionalField: list[String | None] | None = None
        typename__: Annotated[Literal['A'] | None, Field(alias='__typename')] = 'A'

        @property
        def __typename(self) -> Literal['A'] | None:
            return self.typename__
    ```

    <!-- fmt: on -->

---

## `--use-unique-items-as-set` {#use-unique-items-as-set}

Generate set types for arrays with uniqueItems constraint.

The `--use-unique-items-as-set` flag generates Python set types instead of
list types for JSON Schema arrays that have the uniqueItems constraint set
to true, enforcing uniqueness at the type level: a property, a root model,
the member of an anyOf or oneOf, the items of an array and the values of a
map. An array whose items no set can hold, such as objects or arrays, stays
a list. So does a nested array in a union beside another member of its kind
that keeps its lists, an array beside an array or a map beside a map, as a
msgspec union holds one of each.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input api_constrained.yaml --use-unique-items-as-set --field-constraints # (1)!
    ```

    1. :material-arrow-left: `--use-unique-items-as-set` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: "3.0.0"
    info:
      version: 1.0.0
      title: Swagger Petstore
      license:
        name: MIT
    servers:
      - url: http://petstore.swagger.io/v1
    paths:
      /pets:
        get:
          summary: List all pets
          operationId: listPets
          tags:
            - pets
          parameters:
            - name: limit
              in: query
              description: How many items to return at one time (max 100)
              required: false
              schema:
                type: integer
                format: int32
                minimum: 0
                maximum: 100
          responses:
            '200':
              description: A paged array of pets
              headers:
                x-next:
                  description: A link to the next page of responses
                  schema:
                    type: string
              content:
                application/json:
                  schema:
                    $ref: "#/components/schemas/Pets"
            default:
              description: unexpected error
              content:
                application/json:
                  schema:
                    $ref: "#/components/schemas/Error"
                    x-amazon-apigateway-integration:
                      uri:
                        Fn::Sub: arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${PythonVersionFunction.Arn}/invocations
                      passthroughBehavior: when_no_templates
                      httpMethod: POST
                      type: aws_proxy
        post:
          summary: Create a pet
          operationId: createPets
          tags:
            - pets
          responses:
            '201':
              description: Null response
            default:
              description: unexpected error
              content:
                application/json:
                  schema:
                    $ref: "#/components/schemas/Error"
                    x-amazon-apigateway-integration:
                      uri:
                        Fn::Sub: arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${PythonVersionFunction.Arn}/invocations
                      passthroughBehavior: when_no_templates
                      httpMethod: POST
                      type: aws_proxy
      /pets/{petId}:
        get:
          summary: Info for a specific pet
          operationId: showPetById
          tags:
            - pets
          parameters:
            - name: petId
              in: path
              required: true
              description: The id of the pet to retrieve
              schema:
                type: string
          responses:
            '200':
              description: Expected response to a valid request
              content:
                application/json:
                  schema:
                    $ref: "#/components/schemas/Pets"
            default:
              description: unexpected error
              content:
                application/json:
                  schema:
                    $ref: "#/components/schemas/Error"
        x-amazon-apigateway-integration:
          uri:
            Fn::Sub: arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${PythonVersionFunction.Arn}/invocations
          passthroughBehavior: when_no_templates
          httpMethod: POST
          type: aws_proxy
    components:
      schemas:
        Pet:
          required:
            - id
            - name
          properties:
            id:
              type: integer
              format: int64
              minimum: 0
              maximum: 9223372036854775807
            name:
              type: string
              maxLength: 256
            tag:
              type: string
              maxLength: 64
        Pets:
          type: array
          items:
            $ref: "#/components/schemas/Pet"
          maxItems: 10
          minItems: 1
          uniqueItems: true
        UID:
          type: integer
          minimum: 0
        Users:
          type: array
          items:
            required:
              - id
              - name
              - uid
            properties:
              id:
                type: integer
                format: int64
                minimum: 0
              name:
                type: string
                maxLength: 256
              tag:
                type: string
                maxLength: 64
              uid:
                $ref: '#/components/schemas/UID'
              phones:
                type: array
                items:
                  type: string
                  minLength: 3
                maxItems: 10
              fax:
                type: array
                items:
                  type: string
                  minLength: 3
              height:
                type:
                  - integer
                  - number
                minimum: 1
                maximum: 300
              weight:
                type:
                  - number
                  - integer
                minimum: 1.0
                maximum: 1000.0
              age:
                type: integer
                minimum: 0.0
                maximum: 200.0
                exclusiveMinimum: true
              rating:
                type: number
                minimum: 0
                exclusiveMinimum: true
                maximum: 5
    
        Id:
          type: string
        Rules:
          type: array
          items:
            type: string
        Error:
          required:
            - code
            - message
          properties:
            code:
              type: integer
              format: int32
            message:
              type: string
        apis:
          type: array
          items:
            type: object
            properties:
              apiKey:
                type: string
                description: To be used as a dataset parameter value
              apiVersionNumber:
                type: string
                description: To be used as a version parameter value
              apiUrl:
                type: string
                format: uri
                minLength: 1
                description: "The URL describing the dataset's fields"
              apiDocumentationUrl:
                type: string
                format: uri
                description: A URL to the API console for each API
        Event:
          type: object
          properties:
            name:
              type: string
        Result:
            type: object
            properties:
              event:
                $ref: '#/components/schemas/Event'
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  api_constrained.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import AnyUrl, BaseModel, Field, RootModel


    class Pet(BaseModel):
        __hash__ = object.__hash__
        id: int = Field(..., ge=0, le=9223372036854775807)
        name: str = Field(..., max_length=256)
        tag: str | None = Field(None, max_length=64)


    class Pets(RootModel[set[Pet]]):
        root: set[Pet] = Field(..., max_length=10, min_length=1)


    class UID(RootModel[int]):
        root: int = Field(..., ge=0)


    class Phone(RootModel[str]):
        root: str = Field(..., min_length=3)


    class FaxItem(RootModel[str]):
        root: str = Field(..., min_length=3)


    class User(BaseModel):
        id: int = Field(..., ge=0)
        name: str = Field(..., max_length=256)
        tag: str | None = Field(None, max_length=64)
        uid: UID
        phones: list[Phone] | None = Field(None, max_length=10)
        fax: list[FaxItem] | None = None
        height: int | float | None = Field(None, ge=1.0, le=300.0)
        weight: float | int | None = Field(None, ge=1.0, le=1000.0)
        age: int | None = Field(None, gt=0, le=200)
        rating: float | None = Field(None, gt=0.0, le=5.0)


    class Users(RootModel[list[User]]):
        root: list[User]


    class Id(RootModel[str]):
        root: str


    class Rules(RootModel[list[str]]):
        root: list[str]


    class Error(BaseModel):
        code: int
        message: str


    class Api(BaseModel):
        apiKey: str | None = Field(
            None, description='To be used as a dataset parameter value'
        )
        apiVersionNumber: str | None = Field(
            None, description='To be used as a version parameter value'
        )
        apiUrl: AnyUrl | None = Field(
            None, description="The URL describing the dataset's fields"
        )
        apiDocumentationUrl: AnyUrl | None = Field(
            None, description='A URL to the API console for each API'
        )


    class Apis(RootModel[list[Api]]):
        root: list[Api]


    class Event(BaseModel):
        name: str | None = None


    class Result(BaseModel):
        event: Event | None = None
    ```

    <!-- fmt: on -->

---

## `--use-unique-items-as-set-by-value` {#use-unique-items-as-set-by-value}

Write a uniqueItems set only where its final items hash by value.

Under `--use-unique-items-as-set`, the arrays whose schemas declare `uniqueItems` become sets before root models
collapse, type overrides apply and the other modules are processed, and a model that is a set item hashes by identity,
so two equal items are both kept. Deduplication and `--reuse-model` also compare such arrays as the lists they still
are, so a model whose array declares no `uniqueItems` can merge into one whose array does, and a list the schema allows
to repeat then drops its duplicates. With `--use-unique-items-as-set-by-value` the sets are decided on the final item
types: a set is written only where every value of its items hashes by value (scalars, enums, tuples and frozen sets of
them, frozen models whose fields hash), so an array of models that hash only by identity, of `Any` or of containers
stays a list, and models whose arrays differ in `uniqueItems` are kept apart.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input unique_items_by_value.json --use-unique-items-as-set --reuse-model --collapse-reuse-models --collapse-root-models --use-unique-items-as-set-by-value # (1)!
    ```

    1. :material-arrow-left: `--use-unique-items-as-set-by-value` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "title": "Root",
      "type": "object",
      "properties": {
        "labels": {
          "$ref": "#/$defs/Labels"
        },
        "history": {
          "$ref": "#/$defs/History"
        },
        "pets": {
          "type": "array",
          "uniqueItems": true,
          "items": {
            "$ref": "#/$defs/Pet"
          }
        },
        "colors": {
          "type": "array",
          "uniqueItems": true,
          "items": {
            "$ref": "#/$defs/Color"
          }
        },
        "tags": {
          "type": "array",
          "uniqueItems": true,
          "items": {
            "$ref": "#/$defs/Tags"
          }
        },
        "anything": {
          "type": "array",
          "uniqueItems": true
        },
        "numbers": {
          "type": "array",
          "uniqueItems": true,
          "items": {
            "type": "integer"
          },
          "default": [
            1,
            2
          ]
        },
        "first": {
          "type": "object",
          "properties": {
            "entry": {
              "title": "Entry",
              "type": "object",
              "properties": {
                "tags": {
                  "type": "array",
                  "uniqueItems": true,
                  "items": {
                    "type": "string"
                  }
                }
              }
            }
          }
        },
        "second": {
          "type": "object",
          "properties": {
            "entry": {
              "title": "Entry",
              "type": "object",
              "properties": {
                "tags": {
                  "type": "array",
                  "items": {
                    "type": "string"
                  }
                }
              }
            }
          }
        },
        "codes": {
          "type": "array",
          "uniqueItems": true,
          "items": {
            "type": "string"
          }
        },
        "settings": {
          "type": "array",
          "uniqueItems": true,
          "items": {
            "$ref": "#/$defs/Settings"
          }
        },
        "pairs": {
          "type": "array",
          "uniqueItems": true,
          "items": {
            "type": "array",
            "prefixItems": [
              {
                "type": "integer"
              },
              {
                "type": "string"
              }
            ],
            "items": false,
            "minItems": 2
          }
        },
        "limits": {
          "type": "array",
          "uniqueItems": true,
          "items": {
            "$ref": "#/$defs/Limits"
          }
        },
        "points": {
          "type": "array",
          "uniqueItems": true,
          "items": {
            "$ref": "#/$defs/Point"
          }
        }
      },
      "$defs": {
        "Labels": {
          "type": "object",
          "properties": {
            "names": {
              "type": "array",
              "uniqueItems": true,
              "items": {
                "type": "string"
              }
            }
          }
        },
        "History": {
          "type": "object",
          "properties": {
            "names": {
              "type": "array",
              "items": {
                "type": "string"
              }
            }
          }
        },
        "Pet": {
          "type": "object",
          "required": [
            "id"
          ],
          "properties": {
            "id": {
              "type": "integer"
            },
            "nicknames": {
              "type": "array",
              "uniqueItems": true,
              "items": {
                "type": "string"
              }
            }
          }
        },
        "Color": {
          "type": "string",
          "enum": [
            "red",
            "blue"
          ]
        },
        "Tags": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "Settings": {
          "type": "object",
          "properties": {
            "options": {
              "type": "object",
              "default": {}
            }
          }
        },
        "Limits": {
          "type": "object",
          "properties": {
            "levels": {
              "type": "array",
              "uniqueItems": true,
              "items": {
                "type": "integer"
              },
              "default": [
                1
              ]
            }
          }
        },
        "Point": {
          "type": "object",
          "required": [
            "x",
            "y"
          ],
          "properties": {
            "x": {
              "type": "integer"
            },
            "y": {
              "type": "integer"
            }
          }
        }
      },
      "required": [
        "codes"
      ]
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  unique_items_by_value.json

        from __future__ import annotations

        from enum import Enum
        from typing import Any

        from pydantic import BaseModel, Field, RootModel


        class Entry(BaseModel):
            tags: set[str] | None = None


        class First(BaseModel):
            entry: Entry | None = Field(None, title='Entry')


        class Entry1(BaseModel):
            tags: list[str] | None = None


        class Second(BaseModel):
            entry: Entry1 | None = Field(None, title='Entry')


        class Pair(RootModel[list[int | str]]):
            root: list[int | str] = Field(..., max_length=2, min_length=2)


        class Labels(BaseModel):
            names: set[str] | None = None


        class History(BaseModel):
            names: list[str] | None = None


        class Pet(BaseModel):
            id: int
            nicknames: set[str] | None = None


        class Color(Enum):
            red = 'red'
            blue = 'blue'


        class Settings(BaseModel):
            options: dict[str, Any] | None = {}


        class Limits(BaseModel):
            levels: set[int] | None = {1}


        class Point(BaseModel):
            x: int
            y: int


        class Root(BaseModel):
            labels: Labels | None = None
            history: History | None = None
            pets: list[Pet] | None = None
            colors: set[Color] | None = None
            tags: list[list[str]] | None = None
            anything: list[Any] | None = None
            numbers: set[int] | None = {1, 2}
            first: First | None = None
            second: Second | None = None
            codes: set[str]
            settings: list[Settings] | None = None
            pairs: list[Pair] | None = None
            limits: list[Limits] | None = None
            points: list[Point] | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  unique_items_by_value.json

        from __future__ import annotations

        from enum import Enum
        from typing import Any

        from pydantic import BaseModel, Field, RootModel


        class Entry(BaseModel):
            tags: set[str] | None = None


        class First(BaseModel):
            entry: Entry | None = Field(None, title='Entry')


        class Pair(RootModel[list[int | str]]):
            __hash__ = object.__hash__
            root: list[int | str] = Field(..., max_length=2, min_length=2)


        class Labels(BaseModel):
            names: set[str] | None = None


        class Pet(BaseModel):
            __hash__ = object.__hash__
            id: int
            nicknames: set[str] | None = None


        class Color(Enum):
            red = 'red'
            blue = 'blue'


        class Tags(RootModel[list[str]]):
            __hash__ = object.__hash__
            root: list[str]


        class Settings(BaseModel):
            __hash__ = object.__hash__
            options: dict[str, Any] | None = {}


        class Limits(BaseModel):
            __hash__ = object.__hash__
            levels: set[int] | None = {1}


        class Point(BaseModel):
            __hash__ = object.__hash__
            x: int
            y: int


        class Root(BaseModel):
            labels: Labels | None = None
            history: Labels | None = None
            pets: set[Pet] | None = None
            colors: set[Color] | None = None
            tags: set[Tags] | None = None
            anything: set[Any] | None = None
            numbers: set[int] | None = {1, 2}
            first: First | None = None
            second: First | None = None
            codes: set[str]
            settings: set[Settings] | None = None
            pairs: set[Pair] | None = None
            limits: set[Limits] | None = None
            points: set[Point] | None = None
        ```

        <!-- fmt: on -->

---

## `--use-x-python-import-for-every-ref` {#use-x-python-import-for-every-ref}

Type every reference to a schema with `x-python-import` by the class it imports.

A `$ref` to a schema with `x-python-import` at a field is typed by the class it imports. Without this option an allOf
of such a schema and keywords that add no field, such as a `description`, is typed by the model of the schema where a
JSON pointer into another document (`shared.json#/$defs/Amount`), a pointer into the input outside its definitions
(`#/other/Code`) or a whole-document `$ref` (`tally.json`) names it, as in 0.96.0; with it the import types that
allOf as it types the `$ref`, and the model stays written only where it has fields or a root type. Under
`--read-only-write-only-model-type request-response` the option also leaves the readOnly and writeOnly properties of
such a schema out of the request/response split: a model whose field the import types does not split for them, nor
does a model whose allOf bases reach them through such a field.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input order.json --use-x-python-import-for-every-ref # (1)!
    ```

    1. :material-arrow-left: `--use-x-python-import-for-every-ref` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "Order",
      "type": "object",
      "properties": {
        "amount": {"allOf": [{"$ref": "shared.json#/$defs/Amount"}, {"maxLength": 5}]},
        "plain": {"$ref": "shared.json#/$defs/Amount"},
        "rates": {"type": "array", "items": {"allOf": [{"$ref": "shared.json#/$defs/Rate"}, {"minimum": 0}]}},
        "options": {"allOf": [{"$ref": "shared.json#/$defs/Options"}, {"description": "Ordered options."}]},
        "tally": {"allOf": [{"$ref": "tally.json"}, {"description": "Counts by label."}]},
        "entry": {"$ref": "ledger.json#/$defs/Entry"},
        "code": {"allOf": [{"$ref": "#/other/Code"}, {"maxLength": 36}]}
      },
      "other": {
        "Code": {"type": "string", "x-python-import": {"module": "uuid", "name": "UUID"}}
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  order.json

        from __future__ import annotations

        from collections import Counter, OrderedDict
        from decimal import Decimal
        from fractions import Fraction
        from uuid import UUID

        from pydantic import BaseModel, RootModel


        class Amount(RootModel[str]):
            root: str


        class Tally(RootModel[dict[str, int]]):
            root: dict[str, int]


        class Entry(BaseModel):
            label: str | None = None
            totals: OrderedDict | None = None


        class Code(RootModel[str]):
            root: str


        class Order(BaseModel):
            amount: Decimal | None = None
            plain: Decimal | None = None
            rates: list[Fraction] | None = None
            options: OrderedDict | None = None
            tally: Counter | None = None
            entry: Entry | None = None
            code: UUID | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  order.json

        from __future__ import annotations

        from collections import OrderedDict
        from decimal import Decimal
        from fractions import Fraction

        from pydantic import BaseModel, RootModel


        class Amount(RootModel[str]):
            root: str


        class Tally(RootModel[dict[str, int]]):
            root: dict[str, int]


        class Ledger(BaseModel):
            pass


        class Code(RootModel[str]):
            root: str


        class Entry(BaseModel):
            label: str | None = None
            totals: Ledger | None = None


        class Order(BaseModel):
            amount: Amount | None = None
            plain: Decimal | None = None
            rates: list[Fraction] | None = None
            options: OrderedDict | None = None
            tally: Tally | None = None
            entry: Entry | None = None
            code: Code | None = None
        ```

        <!-- fmt: on -->

---

## `--use-x-python-type-as-written` {#use-x-python-type-as-written}

Write a field's `x-python-type` as given: unions of every spelling and container classes.

An `x-python-type` types its field where it is not compatible with the schema's type, and a union spelled `X | Y`, such
as `Literal['a', 'b'] | None`, never is. Without this option the same union spelled `Optional[Literal['a', 'b']]` or
`Union[Literal['low'], Literal['high']]` counts as compatible with every type the schema declares, so it is ignored
and the field takes the schema's `str`. With it every spelling of a union types the field, at a property, an item, a
map value and a root.

A container class compatible with the schema's type, such as `tuple[str, ...]`, `MutableSequence[str]`, `MutableSet`,
`AbstractSet` or `MutableMapping`, is written as that class around the schema's item, key and value types, which keep
their constraints (`tuple[constr(max_length=3), ...]`), where without the option it is written as `list`, `Sequence`,
`set`, `frozenset` or `Mapping`; --use-generic-container-types and --use-unique-items-as-set no longer rewrite it. A
`prefixItems` tuple stays a tuple.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input x_python_type_unions.json --use-x-python-type-as-written # (1)!
    ```

    1. :material-arrow-left: `--use-x-python-type-as-written` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "Settings",
      "type": "object",
      "properties": {
        "mode": {"type": "string", "x-python-type": "Optional[Literal['a', 'b']]"},
        "level": {"type": "string", "x-python-type": "typing.Union[typing.Literal['low'], typing.Literal['high']]"},
        "spelled": {"type": "string", "x-python-type": "Literal['a', 'b'] | None"},
        "modes": {"type": "array", "items": {"type": "string", "x-python-type": "Optional[Literal['a', 'b']]"}},
        "by_key": {"type": "object", "additionalProperties": {"type": "string", "x-python-type": "Union[Literal['x'], Literal['y']]"}},
        "kind": {"$ref": "#/$defs/Kind"}
      },
      "$defs": {
        "Kind": {"type": "string", "x-python-type": "Optional[Literal['k1', 'k2']]"}
      }
    }
    ```

    **Output:**

    === "With Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  x_python_type_unions.json

        from __future__ import annotations

        from typing import Literal, Optional, Union

        from pydantic import BaseModel, RootModel


        class Kind(RootModel[Optional[Literal['k1', 'k2']]]):
            root: Optional[Literal['k1', 'k2']]


        class Settings(BaseModel):
            mode: Optional[Literal['a', 'b']] | None = None
            level: Union[Literal['low'], Literal['high']] | None = None
            spelled: Literal['a', 'b'] | None = None
            modes: list[Optional[Literal['a', 'b']]] | None = None
            by_key: dict[str, Union[Literal['x'], Literal['y']]] | None = None
            kind: Kind | None = None
        ```

        <!-- fmt: on -->

    === "Without Option"

        <!-- fmt: off -->

        ```python
        # generated by datamodel-codegen:
        #   filename:  x_python_type_unions.json

        from __future__ import annotations

        from typing import Literal

        from pydantic import BaseModel, RootModel


        class Kind(RootModel[str]):
            root: str


        class Settings(BaseModel):
            mode: str | None = None
            level: str | None = None
            spelled: Literal['a', 'b'] | None = None
            modes: list[str] | None = None
            by_key: dict[str, str] | None = None
            kind: Kind | None = None
        ```

        <!-- fmt: on -->

---
