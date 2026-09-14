# 🔧 Typing Customization

## 📋 Options

| Option | Description |
|--------|-------------|
| [`--allof-class-hierarchy`](#allof-class-hierarchy) | Controls how allOf schemas are represented in the generated ... |
| [`--allof-merge-mode`](#allof-merge-mode) | Merge constraints from root model references in allOf schema... |
| [`--disable-future-imports`](#disable-future-imports) | Prevent automatic addition of __future__ imports in generate... |
| [`--enum-field-as-literal`](#enum-field-as-literal) | Convert all enum fields to Literal types instead of Enum cla... |
| [`--enum-field-as-literal-map`](#enum-field-as-literal-map) | Override enum/literal generation per-field via JSON mapping. |
| [`--ignore-enum-constraints`](#ignore-enum-constraints) | Ignore enum constraints and use base string type instead of ... |
| [`--import-overrides`](#import-overrides) | Override modules for generated imports by symbol name. |
| [`--no-use-closed-typed-dict`](#no-use-closed-typed-dict) | Disable PEP 728 TypedDict closed/extra_items generation. |
| [`--no-use-specialized-enum`](#no-use-specialized-enum) | Disable specialized Enum classes for Python 3.11+ code gener... |
| [`--no-use-standard-collections`](#no-use-standard-collections) | Use typing.Dict/List instead of built-in dict/list for conta... |
| [`--no-use-union-operator`](#no-use-union-operator) | Use Union[X, Y] / Optional[X] instead of X | Y union operato... |
| [`--output-date-class`](#output-date-class) | Specify date class type for date schema fields. |
| [`--output-datetime-class`](#output-datetime-class) | Specify datetime class type for date-time schema fields. |
| [`--strict-types`](#strict-types) | Enable strict type validation for specified Python types. |
| [`--type-mappings`](#type-mappings) | Override default type mappings for schema formats. |
| [`--type-overrides`](#type-overrides) | Replace schema model types with custom Python types via JSON... |
| [`--use-annotated`](#use-annotated) | Use typing.Annotated for Field() with constraints. |
| [`--use-closed-typed-dict`](#use-closed-typed-dict) | Generate TypedDict with PEP 728 closed/extra_items (default:... |
| [`--use-decimal-for-multiple-of`](#use-decimal-for-multiple-of) | Generate Decimal types for fields with multipleOf constraint... |
| [`--use-generic-container-types`](#use-generic-container-types) | Use generic container types (Sequence, Mapping) for type hin... |
| [`--use-non-positive-negative-number-constrained-types`](#use-non-positive-negative-number-constrained-types) | Use NonPositive/NonNegative types for number constraints. |
| [`--use-object-type`](#use-object-type) | Use object instead of Any for unspecified object and array v... |
| [`--use-pendulum`](#use-pendulum) | Use pendulum types for date, time, and duration fields. |
| [`--use-root-model-type-alias`](#use-root-model-type-alias) | Generate RootModel as type alias format for better mypy supp... |
| [`--use-specialized-enum`](#use-specialized-enum) | Generate StrEnum/IntEnum for string/integer enums (Python 3.... |
| [`--use-standard-collections`](#use-standard-collections) | Use built-in dict/list instead of typing.Dict/List. |
| [`--use-standard-primitive-types`](#use-standard-primitive-types) | Use Python standard library types for string formats instead... |
| [`--use-total-false-for-typed-dict`](#use-total-false-for-typed-dict) | Generate TypedDict declarations with `total=False`. |
| [`--use-tuple-for-fixed-items`](#use-tuple-for-fixed-items) | Generate tuple types for arrays with items array syntax. |
| [`--use-tuple-for-fixed-length-arrays`](#use-tuple-for-fixed-length-arrays) | Generate tuple types for homogeneous fixed-length arrays. |
| [`--use-type-alias`](#use-type-alias) | Use TypeAlias instead of root models for type definitions (e... |
| [`--use-type-alias-type`](#use-type-alias-type) | Use runtime TypeAliasType objects for aliases before Python ... |
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

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --allof-class-hierarchy always # (1)!
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
          "description": "Ref to nested allOf - not a root model.",
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

        ```python
        # generated by datamodel-codegen:
        #   filename:  allof_class_hierarchy.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, Field, constr


        class Entity(BaseModel):
            type: str
            type_list: list[str] | None = ["playground:Entity"]


        class Entity2(BaseModel):
            type: str
            type_list: list[str]


        class Thing(Entity):
            type: str
            type_list: list[str] | None = ["playground:Thing"]
            name: constr(min_length=1) = Field(..., description="The things name")


        class Location(Entity2):
            type: str
            type_list: list[str]
            address: constr(min_length=5) = Field(..., description="The address of the location")


        class Person(Thing, Location):
            name: constr(min_length=1) = Field(..., description="The person's name")
            type: str
            type_list: list[str]
        ```

    === "Without Option"

        ```python
        # generated by datamodel-codegen:
        #   filename:  allof_class_hierarchy.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Any

        from pydantic import BaseModel, Field, constr


        class Person(BaseModel):
            name: constr(min_length=1) = Field(..., description="The things name")
            type: Any | None = "playground:Location"
            type_list: list[Any] | None = [
                "playground:Person",
                "playground:Thing",
                "playground:Location",
            ]
            address: constr(min_length=5) = Field(..., description="The address of the location")


        class Entity(BaseModel):
            type: str
            type_list: list[str] | None = ["playground:Entity"]


        class Entity2(BaseModel):
            type: str
            type_list: list[str]


        class Thing(Entity):
            type: str | None = "playground:Thing"
            type_list: list[str] | None = ["playground:Thing"]
            name: constr(min_length=1) = Field(..., description="The things name")


        class Location(Entity2):
            type: str | None = "playground:Location"
            type_list: list[str] | None = ["playground:Location"]
            address: constr(min_length=5) = Field(..., description="The address of the location")
        ```

---

## `--allof-merge-mode` {#allof-merge-mode}

Merge constraints from root model references in allOf schemas.

The `--allof-merge-mode constraints` merges only constraint properties
(minLength, maximum, etc.) from parent schemas referenced in allOf.
This ensures child schemas inherit validation constraints while keeping
other properties separate.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --allof-merge-mode constraints # (1)!
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

        ```python
        # generated by datamodel-codegen:
        #   filename:  allof_materialize_defaults.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, conint, constr


        class Parent(BaseModel):
            name: constr(min_length=1) | None = "parent_default"
            count: conint(ge=0) | None = 10


        class Child(Parent):
            name: constr(min_length=1, max_length=100) | None = "parent_default"
            count: conint(ge=0, le=1000) | None = 10
        ```

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
              "description": "Ref to nested allOf - not a root model.",
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

            ```python
            # generated by datamodel-codegen:
            #   filename:  allof_root_model_constraints.json
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from typing import Any

            from pydantic import BaseModel, ConfigDict, EmailStr, Field, RootModel, conint, constr


            class StringDatatype(RootModel[constr(pattern=r"^\S(.*\S)?$")]):
                root: constr(pattern=r"^\S(.*\S)?$") = Field(..., description="A base string type.")


            class ConstrainedStringDatatype(RootModel[str]):
                model_config = ConfigDict(
                    regex_engine="python-re",
                )
                root: constr(pattern=r"(?=^\S(.*\S)?$)(?=^[A-Z].*)", min_length=1) = Field(..., description="A constrained string.")


            class IntegerDatatype(RootModel[int]):
                root: int = Field(..., description="A whole number.")


            class NonNegativeIntegerDatatype(RootModel[conint(ge=0)]):
                root: conint(ge=0) = Field(..., description="Non-negative integer.")


            class BoundedIntegerDatatype(RootModel[conint(ge=0, le=100)]):
                root: conint(ge=0, le=100) = Field(..., description="Integer between 0 and 100.")


            class EmailDatatype(RootModel[EmailStr]):
                root: EmailStr = Field(..., description="Email with format.")


            class FormattedStringDatatype(RootModel[EmailStr]):
                root: EmailStr = Field(..., description="A string with email format.")


            class ObjectBase(BaseModel):
                id: int | None = None


            class ObjectWithAllOf(ObjectBase):
                name: str | None = None


            class MultiRefAllOf(BaseModel):
                pass


            class NoConstraintAllOf(RootModel[StringDatatype]):
                root: StringDatatype = Field(..., description="No constraints added.")


            class IncompatibleTypeAllOf(BaseModel):
                pass


            class ConstraintWithProperties(BaseModel):
                extra: str | None = None


            class ConstraintWithItems(BaseModel):
                pass


            class NumberIntegerCompatible(RootModel[conint(ge=0)]):
                root: conint(ge=0) = Field(..., description="Number and integer are compatible.")


            class RefWithSchemaKeywords(RootModel[constr(pattern=r"^\S(.*\S)?$", min_length=5, max_length=100)]):
                root: constr(pattern=r"^\S(.*\S)?$", min_length=5, max_length=100) = Field(
                    ..., description="Ref with additional schema keywords."
                )


            class ArrayDatatype(RootModel[list[str]]):
                root: list[str]


            class RefToArrayAllOf(BaseModel):
                pass


            class ObjectNoPropsDatatype(BaseModel):
                pass


            class RefToObjectNoPropsAllOf(ObjectNoPropsDatatype):
                pass


            class PatternPropsDatatype(RootModel[dict[constr(pattern=r"^S_"), str]]):
                root: dict[constr(pattern=r"^S_"), str]


            class RefToPatternPropsAllOf(BaseModel):
                pass


            class NestedAllOfDatatype(BaseModel):
                pass


            class RefToNestedAllOfAllOf(NestedAllOfDatatype):
                pass


            class ConstraintsOnlyDatatype(RootModel[Any]):
                root: Any = Field(..., description="Constraints only, no type.")


            class RefToConstraintsOnlyAllOf(RootModel[Any]):
                root: Any = Field(..., description="Ref to constraints-only schema.")


            class NoDescriptionAllOf(RootModel[constr(pattern=r"^\S(.*\S)?$", min_length=5)]):
                root: constr(pattern=r"^\S(.*\S)?$", min_length=5) = Field(..., description="A base string type.")


            class EmptyConstraintItemAllOf(RootModel[constr(pattern=r"^\S(.*\S)?$", max_length=50)]):
                root: constr(pattern=r"^\S(.*\S)?$", max_length=50) = Field(..., description="AllOf with empty constraint item.")


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

        === "Without Option"

            ```python
            # generated by datamodel-codegen:
            #   filename:  allof_root_model_constraints.json
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from typing import Any

            from pydantic import BaseModel, EmailStr, Field, RootModel, conint, constr


            class StringDatatype(RootModel[constr(pattern=r"^\S(.*\S)?$")]):
                root: constr(pattern=r"^\S(.*\S)?$") = Field(..., description="A base string type.")


            class ConstrainedStringDatatype(RootModel[constr(pattern=r"^[A-Z].*", min_length=1)]):
                root: constr(pattern=r"^[A-Z].*", min_length=1) = Field(..., description="A constrained string.")


            class IntegerDatatype(RootModel[int]):
                root: int = Field(..., description="A whole number.")


            class NonNegativeIntegerDatatype(RootModel[conint(ge=0)]):
                root: conint(ge=0) = Field(..., description="Non-negative integer.")


            class BoundedIntegerDatatype(RootModel[conint(ge=0, le=100)]):
                root: conint(ge=0, le=100) = Field(..., description="Integer between 0 and 100.")


            class EmailDatatype(RootModel[EmailStr]):
                root: EmailStr = Field(..., description="Email with format.")


            class FormattedStringDatatype(RootModel[EmailStr]):
                root: EmailStr = Field(..., description="A string with email format.")


            class ObjectBase(BaseModel):
                id: int | None = None


            class ObjectWithAllOf(ObjectBase):
                name: str | None = None


            class MultiRefAllOf(BaseModel):
                pass


            class NoConstraintAllOf(RootModel[StringDatatype]):
                root: StringDatatype = Field(..., description="No constraints added.")


            class IncompatibleTypeAllOf(BaseModel):
                pass


            class ConstraintWithProperties(BaseModel):
                extra: str | None = None


            class ConstraintWithItems(BaseModel):
                pass


            class NumberIntegerCompatible(RootModel[conint(ge=0)]):
                root: conint(ge=0) = Field(..., description="Number and integer are compatible.")


            class RefWithSchemaKeywords(RootModel[constr(pattern=r"^\S(.*\S)?$", min_length=5, max_length=100)]):
                root: constr(pattern=r"^\S(.*\S)?$", min_length=5, max_length=100) = Field(
                    ..., description="Ref with additional schema keywords."
                )


            class ArrayDatatype(RootModel[list[str]]):
                root: list[str]


            class RefToArrayAllOf(BaseModel):
                pass


            class ObjectNoPropsDatatype(BaseModel):
                pass


            class RefToObjectNoPropsAllOf(ObjectNoPropsDatatype):
                pass


            class PatternPropsDatatype(RootModel[dict[constr(pattern=r"^S_"), str]]):
                root: dict[constr(pattern=r"^S_"), str]


            class RefToPatternPropsAllOf(BaseModel):
                pass


            class NestedAllOfDatatype(BaseModel):
                pass


            class RefToNestedAllOfAllOf(NestedAllOfDatatype):
                pass


            class ConstraintsOnlyDatatype(RootModel[Any]):
                root: Any = Field(..., description="Constraints only, no type.")


            class RefToConstraintsOnlyAllOf(RootModel[Any]):
                root: Any = Field(..., description="Ref to constraints-only schema.")


            class NoDescriptionAllOf(RootModel[constr(pattern=r"^\S(.*\S)?$", min_length=5)]):
                root: constr(pattern=r"^\S(.*\S)?$", min_length=5) = Field(..., description="A base string type.")


            class EmptyConstraintItemAllOf(RootModel[constr(pattern=r"^\S(.*\S)?$", max_length=50)]):
                root: constr(pattern=r"^\S(.*\S)?$", max_length=50) = Field(..., description="AllOf with empty constraint item.")


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
    datamodel-codegen --input schema.json --disable-future-imports --target-python-version 3.10 # (1)!
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

---

## `--enum-field-as-literal` {#enum-field-as-literal}

Convert all enum fields to Literal types instead of Enum classes.

The `--enum-field-as-literal all` flag converts all enum types to Literal
type annotations. This is useful when you want string literal types instead
of Enum classes for all enumerations.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --enum-field-as-literal all # (1)!
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

        ```python
        # generated by datamodel-codegen:
        #   filename:  enum_models.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum
        from typing import Literal

        from pydantic import BaseModel, Field, RootModel


        class Kind(Enum):
            dog = "dog"
            cat = "cat"


        class Pet(BaseModel):
            id: int
            name: str
            tag: str | None = None
            kind: Kind | None = None
            type: Literal["animal"] | None = None
            number: Literal[1]
            boolean: Literal[True]


        class Pets(RootModel[list[Pet]]):
            root: list[Pet]


        class Kind1(Enum):
            snake = "snake"
            rabbit = "rabbit"


        class Animal(BaseModel):
            kind: Kind1 | None = None


        class Error(BaseModel):
            code: int
            message: str


        class Type(Enum):
            a = "a"
            b = "b"


        class EnumObject(BaseModel):
            type: Type | None = None


        class EnumRoot(Enum):
            a = "a"
            b = "b"


        class IntEnum(Enum):
            number_1 = 1
            number_2 = 2


        class AliasEnum(Enum):
            a = 1
            b = 2
            c = 3


        class MultipleTypeEnum(Enum):
            red = "red"
            amber = "amber"
            green = "green"
            NoneType_None = None
            int_42 = 42


        class SingleEnum(RootModel[Literal["pet"]]):
            root: Literal["pet"]


        class ArrayEnum(RootModel[list[Literal["cat"] | Literal["dog"]]]):
            root: list[Literal["cat"] | Literal["dog"]]


        class NestedVersionEnum(Enum):
            RC1 = "RC1"
            RC1N = "RC1N"
            RC2 = "RC2"
            RC2N = "RC2N"
            RC3 = "RC3"
            RC4 = "RC4"


        class NestedVersion(RootModel[NestedVersionEnum | None]):
            root: NestedVersionEnum | None = Field("RC1", description="nullable enum", examples=["RC2"])


        class NestedNullableEnum(BaseModel):
            nested_version: NestedVersion | None = Field(
                "RC1", description="nullable enum", examples=["RC2"], validate_default=True
            )


        class VersionEnum(Enum):
            RC1 = "RC1"
            RC1N = "RC1N"
            RC2 = "RC2"
            RC2N = "RC2N"
            RC3 = "RC3"
            RC4 = "RC4"


        class Version(RootModel[VersionEnum | None]):
            root: VersionEnum | None = Field("RC1", description="nullable enum", examples=["RC2"])
        ```

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

        ```python
        # generated by datamodel-codegen:
        #   filename:  oneof_const_enum_nested.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Literal

        from pydantic import BaseModel, Field


        class Config(BaseModel):
            mode: Literal["fast", "slow"] | None = Field(None, title="Mode")
            modes: list[Literal["a", "b"]] | None = None
        ```

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


            class Color(RootModel[Literal["BLUE", "GREEN", "RED"]]):
                root: Literal["BLUE", "GREEN", "RED"]


            class EmployeeShiftStatus(RootModel[Literal["NOT_ON_SHIFT", "ON_SHIFT"]]):
                """
                Employee shift status
                """

                root: Literal["NOT_ON_SHIFT", "ON_SHIFT"]


            class EnumWithOneField(RootModel[Literal["FIELD"]]):
                root: Literal["FIELD"]
            ```

        === "Without Option"

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
                BLUE = "BLUE"
                GREEN = "GREEN"
                RED = "RED"


            class EmployeeShiftStatus(Enum):
                """
                Employee shift status
                """

                NOT_ON_SHIFT = "NOT_ON_SHIFT"
                ON_SHIFT = "ON_SHIFT"


            class EnumWithOneField(Enum):
                FIELD = "FIELD"
            ```

---

## `--enum-field-as-literal-map` {#enum-field-as-literal-map}

Override enum/literal generation per-field via JSON mapping.

The `--enum-field-as-literal-map` option allows per-field control over whether
to generate Literal types or Enum classes. Overrides `--enum-field-as-literal`.

You can pass the mapping either inline as JSON or as a path to a JSON file.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --enum-field-as-literal-map '{"status": "literal"}' # (1)!
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

    ```python
    # generated by datamodel-codegen:
    #   filename:  enum_field_as_literal_map.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum
    from typing import Literal

    from pydantic import BaseModel, Field


    class Priority(Enum):
        high = "high"
        medium = "medium"
        low = "low"


    class Category(Enum):
        a = "a"
        b = "b"
        c = "c"


    class EnumFieldAsLiteralMap(BaseModel):
        status: Literal["active", "inactive", "pending"] | None = Field(None, title="Status")
        priority: Priority | None = Field(None, title="Priority")
        category: Category | None = Field(None, title="Category")
    ```

---

## `--ignore-enum-constraints` {#ignore-enum-constraints}

Ignore enum constraints and use base string type instead of Enum classes.

The `--ignore-enum-constraints` flag ignores enum constraints and uses
the base type (str) instead of generating Enum classes. This is useful
when you need flexibility in the values a field can accept beyond the
defined enum members.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --ignore-enum-constraints # (1)!
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

    === "Without Option"

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
            BLUE = "BLUE"
            GREEN = "GREEN"
            RED = "RED"


        class EmployeeShiftStatus(Enum):
            """
            Employee shift status
            """

            NOT_ON_SHIFT = "NOT_ON_SHIFT"
            ON_SHIFT = "ON_SHIFT"


        class EnumWithOneField(Enum):
            FIELD = "FIELD"
        ```

---

## `--import-overrides` {#import-overrides}

Override modules for generated imports by symbol name.

The mapping applies to imports in generated output regardless of their original module, except for
`__future__` imports. This is useful for routing typing helpers through a project compatibility module
across Python target versions.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type typing.TypedDict --target-python-version 3.10 --import-overrides '{"TypedDict": "my_project.typing_compat", "NotRequired": "my_project.typing_compat"}' # (1)!
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

---

## `--no-use-closed-typed-dict` {#no-use-closed-typed-dict}

Disable PEP 728 TypedDict closed/extra_items generation.

Use this option for compatibility with type checkers that don't yet support PEP 728
(e.g., mypy). TypedDict will be generated without `closed=True` or `extra_items`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type typing.TypedDict --no-use-closed-typed-dict # (1)!
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

---

## `--no-use-specialized-enum` {#no-use-specialized-enum}

Disable specialized Enum classes for Python 3.11+ code generation.

The `--no-use-specialized-enum` flag prevents the generator from using
specialized Enum classes (StrEnum, IntEnum) when generating code for
Python 3.11+, falling back to standard Enum classes instead.

**Related:** [`--target-python-version`](model-customization.md#target-python-version), [`--use-specialized-enum`](#use-specialized-enum), [`--use-subclass-enum`](model-customization.md#use-subclass-enum)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --target-python-version 3.11 --no-use-specialized-enum # (1)!
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

        ```python
        # generated by datamodel-codegen:
        #   filename:  subclass_enum.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from enum import Enum

        from pydantic import BaseModel, Field


        class ProcessingStatus(Enum):
            COMPLETED = "COMPLETED"
            PENDING = "PENDING"
            FAILED = "FAILED"


        class ProcessingTask(BaseModel):
            processing_status: ProcessingStatus | None = Field("COMPLETED", title="Status of the task")
        ```

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
            field_1 = "1"
            field_2 = "2"
            field_3 = "3"


        class NonTypedEnum(Enum):
            field_1 = "1"
            field_2 = "2"
            field_3 = "3"


        class BooleanEnum(Enum):
            boolean_True = True
            boolean_False = False


        class UnknownEnum(Enum):
            a = "a"
            b = "b"


        class Model(BaseModel):
            IntEnum_1: IntEnum | None = Field(None, alias="IntEnum")
            FloatEnum_1: FloatEnum | None = Field(None, alias="FloatEnum")
            StrEnum_1: StrEnum | None = Field(None, alias="StrEnum")
            NonTypedEnum_1: NonTypedEnum | None = Field(None, alias="NonTypedEnum")
            BooleanEnum_1: BooleanEnum | None = Field(None, alias="BooleanEnum")
            UnknownEnum_1: UnknownEnum | None = Field(None, alias="UnknownEnum")
        ```

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
            BLUE = "BLUE"
            GREEN = "GREEN"
            RED = "RED"


        class EmployeeShiftStatus(Enum):
            """
            Employee shift status
            """

            NOT_ON_SHIFT = "NOT_ON_SHIFT"
            ON_SHIFT = "ON_SHIFT"


        class EnumWithOneField(Enum):
            FIELD = "FIELD"
        ```

---

## `--no-use-standard-collections` {#no-use-standard-collections}

Use typing.Dict/List instead of built-in dict/list for container types.

The `--no-use-standard-collections` flag generates typing module containers
(Dict, List) instead of built-in types. This is useful for older Python
versions or when explicit typing imports are preferred.

**Related:** [`--use-standard-collections`](#use-standard-collections)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --no-use-standard-collections # (1)!
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
        red = "red"
        green = "green"


    class OneOfResult(BaseModel):
        description: str | None = None


    class AnyOfResult(BaseModel):
        description: str | None = None


    class User(BaseModel):
        name: str | None = None


    class AllOfResult(User):
        description: str | None = None


    class Model(BaseModel):
        test_id: str = Field(..., description="test ID")
        test_ip: str = Field(..., description="test IP")
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

---

## `--no-use-union-operator` {#no-use-union-operator}

Use Union[X, Y] / Optional[X] instead of X | Y union operator.

The `--no-use-union-operator` flag generates union types using typing.Union
and typing.Optional instead of the | operator (PEP 604). This is useful
for older Python versions or when explicit typing imports are preferred.

**Related:** [`--use-union-operator`](#use-union-operator)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --no-use-union-operator # (1)!
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
        red = "red"
        green = "green"


    class OneOfResult(BaseModel):
        description: Optional[str] = None


    class AnyOfResult(BaseModel):
        description: Optional[str] = None


    class User(BaseModel):
        name: Optional[str] = None


    class AllOfResult(User):
        description: Optional[str] = None


    class Model(BaseModel):
        test_id: str = Field(..., description="test ID")
        test_ip: str = Field(..., description="test IP")
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

---

## `--output-date-class` {#output-date-class}

Specify date class type for date schema fields.

The `--output-date-class` flag controls which date type to use for fields
with date format. Options include 'PastDate' for past dates only
or 'FutureDate' for future dates only. This is a Pydantic v2 only feature.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-date-class PastDate # (1)!
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

    ```python
    # generated by datamodel-codegen:
    #   filename:  date_class.yaml
    #   timestamp: 1985-10-26T08:21:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, Field, PastDate


    class Event(BaseModel):
        eventDate: PastDate = Field(..., examples=["2023-12-25"])
    ```

---

## `--output-datetime-class` {#output-datetime-class}

Specify datetime class type for date-time schema fields.

The `--output-datetime-class` flag controls which datetime type to use for fields
with date-time format. Options include 'AwareDatetime' for timezone-aware datetimes
or 'datetime' for standard Python datetime objects.

**See also:** [Type Mappings and Custom Types](../type-mappings.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-datetime-class AwareDatetime # (1)!
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

    ```python
    # generated by datamodel-codegen:
    #   filename:  datetime.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import AwareDatetime, BaseModel, Field


    class InventoryItem(BaseModel):
        releaseDate: AwareDatetime = Field(..., examples=["2016-08-29T09:12:33.001Z"])
    ```

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
    datamodel-codegen --input schema.json --strict-types str bytes int float bool # (1)!
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

    ```python
    # generated by datamodel-codegen:
    #   filename:  strict_types.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import (
        BaseModel,
        Field,
        StrictBool,
        StrictBytes,
        StrictInt,
        StrictStr,
        confloat,
        conint,
        constr,
    )


    class User(BaseModel):
        name: StrictStr | None = Field(None, examples=["ken"])
        age: StrictInt | None = None
        salary: conint(ge=0, strict=True) | None = None
        debt: conint(le=0, strict=True) | None = None
        loan: confloat(le=0.0, strict=True) | None = None
        tel: constr(pattern=r"^(\([0-9]{3}\))?[0-9]{3}-[0-9]{4}$", strict=True) | None = None
        height: confloat(ge=0.0, strict=True) | None = None
        weight: confloat(ge=0.0, strict=True) | None = None
        score: confloat(ge=1e-08, strict=True) | None = None
        active: StrictBool | None = None
        photo: StrictBytes | None = None
    ```

---

## `--type-mappings` {#type-mappings}

Override default type mappings for schema formats.

The `--type-mappings` flag configures the code generation behavior.

**See also:** [Type Mappings and Custom Types](../type-mappings.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type pydantic_v2.BaseModel --type-mappings binary=string # (1)!
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

    ```python
    # generated by datamodel-codegen:
    #   filename:  type_mappings.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import Base64Str, BaseModel, Field


    class BlobModel(BaseModel):
        content: str = Field(..., description="Binary content that should be mapped to string")
        data: Base64Str = Field(..., description="Base64 encoded data")
        name: str = Field(..., description="Regular string field")
    ```

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
    datamodel-codegen --input schema.json --type-overrides '{"CustomType": "my_app.types.CustomType"}' # (1)!
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
    datamodel-codegen --input schema.json --output-model-type pydantic_v2.BaseModel --use-annotated # (1)!
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
            apiKey: Annotated[str | None, Field(description="To be used as a dataset parameter value")] = None
            apiVersionNumber: Annotated[str | None, Field(description="To be used as a version parameter value")] = None
            apiUrl: Annotated[AnyUrl | None, Field(description="The URL describing the dataset's fields")] = None
            apiDocumentationUrl: Annotated[AnyUrl | None, Field(description="A URL to the API console for each API")] = None


        class Apis(RootModel[list[Api]]):
            root: list[Api]


        class Event(BaseModel):
            name: str | None = None


        class Result(BaseModel):
            event: Event | None = None
        ```

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
            typename__: Annotated[Literal["A"] | None, Field(alias="__typename")] = "A"
        ```

---

## `--use-closed-typed-dict` {#use-closed-typed-dict}

Generate TypedDict with PEP 728 closed/extra_items (default: enabled).

When enabled (default), generates TypedDict with PEP 728 `closed=True` or `extra_items`
parameters based on `additionalProperties` constraints in the schema.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type typing.TypedDict --use-closed-typed-dict # (1)!
    ```

    1. :material-arrow-left: `--use-closed-typed-dict` - the option documented here

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: cli-reference.typing-customization.use-closed-typed-dict.example -->
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
    ```python
    from __future__ import annotations

    from typing_extensions import NotRequired, TypedDict


    class ClosedModel(TypedDict, closed=True):
        name: str
        age: NotRequired[int]
    ```
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
    datamodel-codegen --input schema.json --use-decimal-for-multiple-of # (1)!
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

    ```python
    # generated by datamodel-codegen:
    #   filename:  use_decimal_for_multiple_of.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from decimal import Decimal

    from pydantic import BaseModel, condecimal, confloat


    class Model(BaseModel):
        price: condecimal(ge=Decimal("0"), le=Decimal("99999.99"), multiple_of=Decimal("0.01")) | None = None
        quantity: condecimal(multiple_of=Decimal("0.1")) | None = None
        rate: condecimal(multiple_of=Decimal("0.001"), lt=Decimal("1.0"), gt=Decimal("0.0")) | None = None
        simple_float: confloat(ge=0.0, le=100.0) | None = None
    ```

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
    datamodel-codegen --input schema.json --use-generic-container-types # (1)!
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
        red = "red"
        green = "green"


    class OneOfResult(BaseModel):
        description: str | None = None


    class AnyOfResult(BaseModel):
        description: str | None = None


    class User(BaseModel):
        name: str | None = None


    class AllOfResult(User):
        description: str | None = None


    class Model(BaseModel):
        test_id: str = Field(..., description="test ID")
        test_ip: str = Field(..., description="test IP")
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

---

## `--use-non-positive-negative-number-constrained-types` {#use-non-positive-negative-number-constrained-types}

Use NonPositive/NonNegative types for number constraints.

The `--use-non-positive-negative-number-constrained-types` flag generates
Pydantic's NonPositiveInt, NonNegativeInt, NonPositiveFloat, and NonNegativeFloat
types for fields with minimum: 0 or maximum: 0 constraints, instead of using
conint/confloat with ge/le parameters.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-non-positive-negative-number-constrained-types # (1)!
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
        non_negative_int: NonNegativeInt | None = Field(None, description="NonNegativeInt: minimum=0 only")
        non_positive_int: NonPositiveInt | None = Field(None, description="NonPositiveInt: maximum=0 only")
        non_negative_float: NonNegativeFloat | None = Field(None, description="NonNegativeFloat: minimum=0 only")
        non_positive_float: NonPositiveFloat | None = Field(None, description="NonPositiveFloat: maximum=0 only")
        positive_int: PositiveInt | None = Field(None, description="PositiveInt: exclusiveMinimum=0")
        negative_int: NegativeInt | None = Field(None, description="NegativeInt: exclusiveMaximum=0")
        positive_float: PositiveFloat | None = Field(None, description="PositiveFloat: exclusiveMinimum=0")
        negative_float: NegativeFloat | None = Field(None, description="NegativeFloat: exclusiveMaximum=0")
        bounded_non_negative_int: conint(ge=0, le=100) | None = Field(
            None, description="NonNegativeInt with additional upper bound"
        )
        bounded_non_positive_int: conint(ge=-100, le=0) | None = Field(
            None, description="NonPositiveInt with additional lower bound"
        )
        bounded_non_negative_float: confloat(ge=0.0, le=1.0) | None = Field(
            None, description="NonNegativeFloat with additional upper bound"
        )
        bounded_non_positive_float: confloat(ge=-1.0, le=0.0) | None = Field(
            None, description="NonPositiveFloat with additional lower bound"
        )
        plain_constrained_int: conint(ge=5, le=100) | None = Field(
            None, description="No zero bound: should remain conint/Field"
        )
    ```

---

## `--use-object-type` {#use-object-type}

Use object instead of Any for unspecified object and array values.

The `--use-object-type` flag configures the code generation to use `object`
instead of `Any` when JSON Schema does not define object properties or array
item schemas. This helps generate stricter type hints for unstructured values.

**Related:** [`--output-model-type`](model-customization.md#output-model-type), [`--target-python-version`](model-customization.md#target-python-version)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type typing.TypedDict --target-python-version 3.10 --use-object-type # (1)!
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
    datamodel-codegen --input schema.json --use-pendulum # (1)!
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

---

## `--use-root-model-type-alias` {#use-root-model-type-alias}

Generate RootModel as type alias format for better mypy support.

When enabled, root models with simple types are generated as type aliases
instead of class definitions, improving mypy type inference.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-root-model-type-alias --output-model-type pydantic_v2.BaseModel # (1)!
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
    datamodel-codegen --input schema.json --target-python-version 3.11 --use-specialized-enum # (1)!
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
        field_1 = "1"
        field_2 = "2"
        field_3 = "3"


    class NonTypedEnum(Enum):
        field_1 = "1"
        field_2 = "2"
        field_3 = "3"


    class BooleanEnum(Enum):
        boolean_True = True
        boolean_False = False


    class UnknownEnum(Enum):
        a = "a"
        b = "b"


    class Model(BaseModel):
        IntEnum: IntEnumModel | None = None
        FloatEnum_1: FloatEnum | None = Field(None, alias="FloatEnum")
        StrEnum: StrEnumModel | None = None
        NonTypedEnum_1: NonTypedEnum | None = Field(None, alias="NonTypedEnum")
        BooleanEnum_1: BooleanEnum | None = Field(None, alias="BooleanEnum")
        UnknownEnum_1: UnknownEnum | None = Field(None, alias="UnknownEnum")
    ```

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
    datamodel-codegen --input schema.json --use-standard-collections # (1)!
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
        red = "red"
        green = "green"


    class OneOfResult(BaseModel):
        description: str | None = None


    class AnyOfResult(BaseModel):
        description: str | None = None


    class User(BaseModel):
        name: str | None = None


    class AllOfResult(User):
        description: str | None = None


    class Model(BaseModel):
        test_id: str = Field(..., description="test ID")
        test_ip: str = Field(..., description="test IP")
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
    datamodel-codegen --input schema.json --output-model-type dataclasses.dataclass --use-standard-primitive-types # (1)!
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

---

## `--use-total-false-for-typed-dict` {#use-total-false-for-typed-dict}

Generate TypedDict declarations with `total=False`.

Optional fields are left unwrapped, while required fields are marked with
`Required[...]`. This can substantially reduce generated code when most fields
are optional.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type typing.TypedDict --use-total-false-for-typed-dict --use-frozen-field # (1)!
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
            "FunctionalModel",
            {
                "base_required": Required[str],
                "base_optional": int,
                "invalid-key": Required[str],
                "child_optional": bool,
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

    === "Python 3.11"

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
            "FunctionalModel",
            {
                "base_required": Required[str],
                "base_optional": int,
                "invalid-key": Required[str],
                "child_optional": bool,
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

---

## `--use-tuple-for-fixed-items` {#use-tuple-for-fixed-items}

Generate tuple types for arrays with items array syntax.

When `--use-tuple-for-fixed-items` is enabled and an array has `items` as an array
with `minItems == maxItems == len(items)`, generate a tuple type instead of a list.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-tuple-for-fixed-items # (1)!
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

    ```python
    # generated by datamodel-codegen:
    #   filename:  items_array_tuple.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Model(BaseModel):
        point: tuple[float, float]
    ```

---

## `--use-tuple-for-fixed-length-arrays` {#use-tuple-for-fixed-length-arrays}

Generate tuple types for homogeneous fixed-length arrays.

When `--use-tuple-for-fixed-length-arrays` is enabled and an array has one
`items` schema with `minItems == maxItems`, generate a tuple type instead of a
list. An empty fixed-length array becomes `tuple[()]`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-tuple-for-fixed-length-arrays --output-model-type pydantic_v2.BaseModel # (1)!
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
    datamodel-codegen --input schema.json --use-type-alias # (1)!
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
                Field(..., description="An annotated union type", title="MyAnnotatedType"),
            ],
        )


        class ModelWithTypeAliasField(BaseModel):
            simple_field: SimpleString | None = None
            union_field: UnionType | None = None
            array_field: ArrayType | None = None
            annotated_field: AnnotatedType | None = None
        ```

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
            typename__: Literal["Person"] | None = Field("Person", alias="__typename")


        class Pet(BaseModel):
            name: String
            type: String
            typename__: Literal["Pet"] | None = Field("Pet", alias="__typename")


        UnionType = TypeAliasType(
            "UnionType",
            Union[
                "Person",
                "Pet",
            ],
        )


        class ModelWithTypeAliasField(BaseModel):
            simple_field: SimpleString | None = None
            string_field: String | None = None
            union_field: UnionType | None = None
            typename__: Literal["ModelWithTypeAliasField"] | None = Field("ModelWithTypeAliasField", alias="__typename")
        ```

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
    datamodel-codegen --input schema.json --output-model-type typing.TypedDict --use-type-alias-type --target-python-version 3.10 # (1)!
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

    === "Python 3.12"

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
    datamodel-codegen --input schema.json --output-model-type pydantic_v2.BaseModel --use-annotated --use-union-operator # (1)!
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
        typename__: Annotated[Literal["A"] | None, Field(alias="__typename")] = "A"
    ```

---

## `--use-unique-items-as-set` {#use-unique-items-as-set}

Generate set types for arrays with uniqueItems constraint.

The `--use-unique-items-as-set` flag generates Python set types instead of
list types for JSON Schema arrays that have the uniqueItems constraint set
to true, enforcing uniqueness at the type level.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-unique-items-as-set --field-constraints # (1)!
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

    ```python
    # generated by datamodel-codegen:
    #   filename:  api_constrained.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import AnyUrl, BaseModel, Field


    class Pet(BaseModel):
        id: int = Field(..., ge=0, le=9223372036854775807)
        name: str = Field(..., max_length=256)
        tag: str | None = Field(None, max_length=64)


    class Pets(BaseModel):
        __root__: set[Pet] = Field(..., max_items=10, min_items=1, unique_items=True)


    class UID(BaseModel):
        __root__: int = Field(..., ge=0)


    class Phone(BaseModel):
        __root__: str = Field(..., min_length=3)


    class FaxItem(BaseModel):
        __root__: str = Field(..., min_length=3)


    class User(BaseModel):
        id: int = Field(..., ge=0)
        name: str = Field(..., max_length=256)
        tag: str | None = Field(None, max_length=64)
        uid: UID
        phones: list[Phone] | None = Field(None, max_items=10)
        fax: list[FaxItem] | None = None
        height: int | float | None = Field(None, ge=1.0, le=300.0)
        weight: float | int | None = Field(None, ge=1.0, le=1000.0)
        age: int | None = Field(None, gt=0, le=200)
        rating: float | None = Field(None, gt=0.0, le=5.0)


    class Users(BaseModel):
        __root__: list[User]


    class Id(BaseModel):
        __root__: str


    class Rules(BaseModel):
        __root__: list[str]


    class Error(BaseModel):
        code: int
        message: str


    class Api(BaseModel):
        apiKey: str | None = Field(None, description="To be used as a dataset parameter value")
        apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
        apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
        apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")


    class Apis(BaseModel):
        __root__: list[Api]


    class Event(BaseModel):
        name: str | None = None


    class Result(BaseModel):
        event: Event | None = None
    ```

---
