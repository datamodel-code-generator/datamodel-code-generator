# 🔧 Typing Customization

## 📋 Options

| Option | Description |
|--------|-------------|
| [`--allof-class-hierarchy`](#allof-class-hierarchy) | Controls how allOf schemas are represented in the generated ... |
| [`--allof-merge-mode`](#allof-merge-mode) | Merge constraints from root model references in allOf schema... |
| [`--disable-future-imports`](#disable-future-imports) | Prevent automatic addition of __future__ imports in generate... |
| [`--enum-field-as-literal`](#enum-field-as-literal) | Convert all enum fields to Literal types instead of Enum cla... |
| [`--enum-field-as-literal-map`](#enum-field-as-literal-map) | Override enum/literal generation per-field via JSON mapping. |
| [`--filter-literal-values`](#filter-literal-values) | Keep only the values of a `const` or an `enum` that meet the... |
| [`--ignore-enum-constraints`](#ignore-enum-constraints) | Ignore enum constraints and use base string type instead of ... |
| [`--import-overrides`](#import-overrides) | Override modules for generated imports by symbol name. |
| [`--intersect-allof-keywords`](#intersect-allof-keywords) | Combine the keywords of every part of an `allOf`, a `$ref` w... |
| [`--keep-enum-member-values`](#keep-enum-member-values) | Give an enum class a value base only when it keeps every mem... |
| [`--no-use-closed-typed-dict`](#no-use-closed-typed-dict) | Disable PEP 728 TypedDict closed/extra_items generation. |
| [`--no-use-specialized-enum`](#no-use-specialized-enum) | Disable specialized Enum classes for Python 3.11+ code gener... |
| [`--no-use-standard-collections`](#no-use-standard-collections) | Use typing.Dict/List instead of built-in dict/list for conta... |
| [`--no-use-union-operator`](#no-use-union-operator) | Use Union[X, Y] / Optional[X] instead of X | Y union operato... |
| [`--output-date-class`](#output-date-class) | Specify date class type for date schema fields. |
| [`--output-datetime-class`](#output-datetime-class) | Specify datetime class type for date-time schema fields. |
| [`--read-const-beside-enum`](#read-const-beside-enum) | Read a `const` beside an `enum` as JSON Schema does. |
| [`--read-unevaluated-properties`](#read-unevaluated-properties) | Read a schema-valued `unevaluatedProperties` as `additionalP... |
| [`--strict-types`](#strict-types) | Enable strict type validation for specified Python types. |
| [`--type-mappings`](#type-mappings) | Override default type mappings for schema formats. |
| [`--type-overrides`](#type-overrides) | Replace schema model types with custom Python types via JSON... |
| [`--use-annotated`](#use-annotated) | Use typing.Annotated for Field() with constraints. |
| [`--use-closed-typed-dict`](#use-closed-typed-dict) | Generate TypedDict with PEP 728 closed/extra_items (default:... |
| [`--use-decimal-for-multiple-of`](#use-decimal-for-multiple-of) | Generate Decimal types for fields with multipleOf constraint... |
| [`--use-decimal-in-field-forms`](#use-decimal-in-field-forms) | Write `Decimal` for a number with `multipleOf` in the field ... |
| [`--use-generic-container-types`](#use-generic-container-types) | Use generic container types (Sequence, Mapping) for type hin... |
| [`--use-non-positive-negative-number-constrained-types`](#use-non-positive-negative-number-constrained-types) | Use NonPositive/NonNegative types for number constraints. |
| [`--use-object-type`](#use-object-type) | Use object instead of Any for unspecified object and array v... |
| [`--use-pendulum`](#use-pendulum) | Use pendulum types for date, time, and duration fields. |
| [`--use-root-model-type-alias`](#use-root-model-type-alias) | Generate RootModel as type alias format for better mypy supp... |
| [`--use-single-dict-for-object-maps`](#use-single-dict-for-object-maps) | Type an object map as one dict instead of a union of dicts. |
| [`--use-single-value-enum-as-const`](#use-single-value-enum-as-const) | Treat `enum` with a single value as `const` with that value. |
| [`--use-specialized-enum`](#use-specialized-enum) | Generate StrEnum/IntEnum for string/integer enums (Python 3.... |
| [`--use-standard-collections`](#use-standard-collections) | Use built-in dict/list instead of typing.Dict/List. |
| [`--use-standard-primitive-types`](#use-standard-primitive-types) | Use Python standard library types for string formats instead... |
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

**See also:** [Python Version Compatibility](../python-version-compatibility.md)

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

**See also:** [Type Mappings and Custom Types](../type-mappings.md)

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

**See also:** [Type Mappings and Custom Types](../type-mappings.md)

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
            closed_by_type_list: list[str] | None = Field(None, max_length=2, min_length=2)
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


        class Pet(BaseModel):
            name: String
            type: String
            typename__: Literal['Pet'] | None = Field('Pet', alias='__typename')


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
    ```

    <!-- fmt: on -->

---

## `--use-unique-items-as-set` {#use-unique-items-as-set}

Generate set types for arrays with uniqueItems constraint.

The `--use-unique-items-as-set` flag generates Python set types instead of
list types for JSON Schema arrays that have the uniqueItems constraint set
to true, enforcing uniqueness at the type level.

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
