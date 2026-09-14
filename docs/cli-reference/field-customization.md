# 🏷️ Field Customization

## 📋 Options

| Option | Description |
|--------|-------------|
| [`--aliases`](#aliases) | Apply custom field and class name aliases via inline JSON or... |
| [`--capitalize-enum-members`](#capitalize-enum-members) | Capitalize enum member names to UPPER_CASE format. |
| [`--default-values`](#default-values) | Override field default values via inline JSON or a JSON file... |
| [`--deserialize-default-values`](#deserialize-default-values) | Deserialize selected direct scalar field defaults into their... |
| [`--empty-enum-field-name`](#empty-enum-field-name) | Name for empty string enum field values. |
| [`--extra-fields`](#extra-fields) | Configure how generated models handle extra fields not defin... |
| [`--field-constraints`](#field-constraints) | Generate Field() with validation constraints from schema. |
| [`--field-extra-keys`](#field-extra-keys) | Include specific extra keys in Field() definitions. |
| [`--field-extra-keys-without-x-prefix`](#field-extra-keys-without-x-prefix) | Include schema extension keys in Field() without requiring '... |
| [`--field-include-all-keys`](#field-include-all-keys) | Include all schema keys in Field() json_schema_extra. |
| [`--field-type-collision-strategy`](#field-type-collision-strategy) | Rename type class instead of field when names collide (Pydan... |
| [`--infer-union-variant-names`](#infer-union-variant-names) | Infer names for inline oneOf/anyOf object variants from lite... |
| [`--no-alias`](#no-alias) | Disable Field alias generation for non-Python-safe property ... |
| [`--no-deserialize-default-values`](#no-deserialize-default-values) | Keep serialized schema defaults instead of deserializing sel... |
| [`--original-field-name-delimiter`](#original-field-name-delimiter) | Specify delimiter for original field names when using snake-... |
| [`--remove-special-field-name-prefix`](#remove-special-field-name-prefix) | Remove the special prefix from field names. |
| [`--serialization-aliases`](#serialization-aliases) | Apply custom Pydantic v2 serialization aliases via inline JS... |
| [`--set-default-enum-member`](#set-default-enum-member) | Use the legacy flag for deserializing enum defaults. |
| [`--snake-case-field`](#snake-case-field) | Convert field names to snake_case format. |
| [`--special-field-name-prefix`](#special-field-name-prefix) | Prefix to add to special field names (like reserved keywords... |
| [`--use-attribute-docstrings`](#use-attribute-docstrings) | Generate field descriptions as attribute docstrings instead ... |
| [`--use-enum-values-in-discriminator`](#use-enum-values-in-discriminator) | Use enum values in discriminator mappings for union types. |
| [`--use-field-description`](#use-field-description) | Include schema descriptions as Field docstrings. |
| [`--use-field-description-example`](#use-field-description-example) | Add field examples to docstrings. |
| [`--use-inline-field-description`](#use-inline-field-description) | Add field descriptions as inline comments. |
| [`--use-schema-description`](#use-schema-description) | Use schema description as class docstring. |
| [`--use-serialization-alias`](#use-serialization-alias) | Use serialization_alias instead of alias for field aliasing ... |
| [`--use-single-line-docstring`](#use-single-line-docstring) | Emit short docstrings on a single line. |
| [`--use-title-as-name`](#use-title-as-name) | Use schema title as the generated class name. |

---

## 🍳 Recipes

### Normalize incoming field names

Convert source names to Python identifiers while preserving explicit alias data for runtime IO.

**Options:** [`--snake-case-field`](#snake-case-field), [`--original-field-name-delimiter`](#original-field-name-delimiter), [`--aliases`](#aliases)

### Carry schema documentation into models

Promote schema and field descriptions into generated docstrings or field metadata.

**Options:** [`--use-schema-description`](#use-schema-description), [`--use-field-description`](#use-field-description), [`--use-field-description-example`](#use-field-description-example)

---

## `--aliases` {#aliases}

Apply custom field and class name aliases via inline JSON or a JSON file path.

The `--aliases` option allows renaming fields and classes via an inline JSON object or JSON mapping file,
providing fine-grained control over generated names independent of schema definitions.

**See also:** [Field Aliases](../aliases.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --aliases openapi/aliases.json --target-python-version 3.10 # (1)!
    ```

    1. :material-arrow-left: `--aliases` - the option documented here

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
                  default: 1
                name:
                  type: string
                tag:
                  type: string
            Pets:
              type: array
              items:
                $ref: "#/components/schemas/Pet"
            Users:
              type: array
              items:
                required:
                  - id
                  - name
                properties:
                  id:
                    type: integer
                    format: int64
                  name:
                    type: string
                  tag:
                    type: string
            Id:
              type: string
            Rules:
              type: array
              items:
                type: string
            Error:
              description: error result
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
                    description: "The URL describing the dataset's fields"
                  apiDocumentationUrl:
                    type: string
                    format: uri
                    description: A URL to the API console for each API
            Event:
              type: object
              description: Event object
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

        === "msgspec"

            ```python
            # generated by datamodel-codegen:
            #   filename:  api.yaml
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from typing import Annotated, TypeAlias

            from msgspec import UNSET, Meta, Struct, UnsetType, field


            class Pet(Struct):
                id_: int = field(name="id")
                name_: str = field(name="name")
                tag: str | UnsetType = UNSET


            Pets: TypeAlias = list[Pet]


            class User(Struct):
                id_: int = field(name="id")
                name_: str = field(name="name")
                tag: str | UnsetType = UNSET


            Users: TypeAlias = list[User]


            Id: TypeAlias = str


            Rules: TypeAlias = list[str]


            class Error(Struct):
                code: int
                message: str


            class Api(Struct):
                apiKey: Annotated[str, Meta(description="To be used as a dataset parameter value")] | UnsetType = UNSET
                apiVersionNumber: Annotated[str, Meta(description="To be used as a version parameter value")] | UnsetType = UNSET
                apiUrl: Annotated[str, Meta(description="The URL describing the dataset's fields")] | UnsetType = UNSET
                apiDocumentationUrl: Annotated[str, Meta(description="A URL to the API console for each API")] | UnsetType = UNSET


            Apis: TypeAlias = list[Api]


            class Event(Struct):
                name_: str | UnsetType = field(name="name", default=UNSET)


            class Result(Struct):
                event: Event | UnsetType = UNSET
            ```

    === "JSON Schema"

        **Input Schema:**

        ```json
        {
          "$schema": "http://json-schema.org/draft-07/schema#",
          "type": "object",
          "title": "Root",
          "properties": {
            "name": {
              "type": "string"
            },
            "user": {
              "type": "object",
              "title": "User",
              "properties": {
                "name": {
                  "type": "string"
                },
                "id": {
                  "type": "integer"
                }
              }
            },
            "address": {
              "type": "object",
              "title": "Address",
              "properties": {
                "name": {
                  "type": "string"
                },
                "city": {
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
        #   filename:  hierarchical_aliases.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, Field


        class User(BaseModel):
            user_name: str | None = Field(None, alias="name")
            id: int | None = None


        class Address(BaseModel):
            address_name: str | None = Field(None, alias="name")
            city: str | None = None


        class Root(BaseModel):
            root_name: str | None = Field(None, alias="name")
            user: User | None = Field(None, title="User")
            address: Address | None = Field(None, title="Address")
        ```

    === "GraphQL"

        **Input Schema:**

        ```graphql
        scalar DateTime
        
        type DateTimePeriod {
            from: DateTime!
            to: DateTime!
        }
        ```

        **Output:**

        ```python
        # generated by datamodel-codegen:
        #   filename:  field-aliases.graphql
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Literal

        from pydantic import BaseModel, Field
        from typing_extensions import TypeAliasType

        Boolean = TypeAliasType("Boolean", bool)
        """
        The `Boolean` scalar type represents `true` or `false`.
        """


        DateTime = TypeAliasType("DateTime", str)


        String = TypeAliasType("String", str)
        """
        The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
        """


        class DateTimePeriod(BaseModel):
            periodFrom: DateTime = Field(..., alias="from")
            periodTo: DateTime = Field(..., alias="to")
            typename__: Literal["DateTimePeriod"] | None = Field("DateTimePeriod", alias="__typename")
        ```

---

## `--capitalize-enum-members` {#capitalize-enum-members}

Capitalize enum member names to UPPER_CASE format.

The `--capitalize-enum-members` flag converts enum member names to
UPPER_CASE format (e.g., `active` becomes `ACTIVE`), following Python
naming conventions for constants.

**Aliases:** `--capitalise-enum-members` | **Related:** [`--snake-case-field`](#snake-case-field)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --capitalize-enum-members # (1)!
    ```

    1. :material-arrow-left: `--capitalize-enum-members` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "string",
      "enum": [
        "snake_case",
        "CAP_CASE",
        "CamelCase",
        "UPPERCASE"
      ]
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  many_case_enum.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum


    class Model(Enum):
        SNAKE_CASE = "snake_case"
        CAP_CASE = "CAP_CASE"
        CAMEL_CASE = "CamelCase"
        UPPERCASE = "UPPERCASE"
    ```

---

## `--default-values` {#default-values}

Override field default values via inline JSON or a JSON file path.

The `--default-values` option allows specifying default values for fields via an inline JSON object or JSON file.
Supports scoped format (ClassName.field) for hierarchical overrides.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --default-values default_values/scoped_defaults.json # (1)!
    ```

    1. :material-arrow-left: `--default-values` - the option documented here

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
              "type": "string"
            },
            "status": {
              "type": "string"
            },
            "page": {
              "type": "integer"
            }
          },
          "required": ["name"]
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  default_values_override.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel, RootModel


    class Model(RootModel[Any]):
        root: Any


    class User(BaseModel):
        name: str
        status: str | None = "active"
        page: int | None = 1
    ```

---

## `--deserialize-default-values` {#deserialize-default-values}

Deserialize selected direct scalar field defaults into their generated Python types.

The `--deserialize-default-values` option accepts an explicit list of generated value types. For example,
`--deserialize-default-values decimal` emits a direct Decimal field's valid string default as a `Decimal` value,
while `--deserialize-default-values enum` emits matching scalar and list defaults as enum members. Defaults that
cannot be deserialized remain serialized so the generated module stays importable.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --deserialize-default-values decimal --target-python-version 3.10 # (1)!
    ```

    1. :material-arrow-left: `--deserialize-default-values` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Invoice",
      "type": "object",
      "properties": {
        "amount": {
          "type": "string",
          "format": "decimal",
          "default": "12.340"
        },
        "invalid_amount": {
          "type": "string",
          "format": "decimal",
          "default": "not-a-decimal"
        },
        "nullable_amount": {
          "type": ["string", "null"],
          "format": "decimal",
          "default": null
        },
        "label": {
          "type": "string",
          "default": "retail"
        }
      }
    }
    ```

    **Output:**

    === "Pydantic v2"

        ```python
        # generated by datamodel-codegen:
        #   filename:  serialized_decimal_defaults.json

        from __future__ import annotations

        from decimal import Decimal

        from pydantic import BaseModel


        class Invoice(BaseModel):
            amount: Decimal | None = Decimal("12.340")
            invalid_amount: Decimal | None = "not-a-decimal"
            nullable_amount: Decimal | None = None
            label: str | None = "retail"
        ```

    === "Pydantic v2 dataclass"

        ```python
        # generated by datamodel-codegen:
        #   filename:  serialized_decimal_defaults.json

        from __future__ import annotations

        from decimal import Decimal

        from pydantic.dataclasses import dataclass


        @dataclass
        class Invoice:
            amount: Decimal | None = Decimal("12.340")
            invalid_amount: Decimal | None = "not-a-decimal"
            nullable_amount: Decimal | None = None
            label: str | None = "retail"
        ```

    === "dataclass"

        ```python
        # generated by datamodel-codegen:
        #   filename:  serialized_decimal_defaults.json

        from __future__ import annotations

        from dataclasses import dataclass
        from decimal import Decimal


        @dataclass
        class Invoice:
            amount: Decimal | None = Decimal("12.340")
            invalid_amount: Decimal | None = "not-a-decimal"
            nullable_amount: Decimal | None = None
            label: str | None = "retail"
        ```

    === "TypedDict"

        ```python
        # generated by datamodel-codegen:
        #   filename:  serialized_decimal_defaults.json

        from __future__ import annotations

        from decimal import Decimal
        from typing import TypedDict

        from typing_extensions import NotRequired


        class Invoice(TypedDict):
            amount: NotRequired[Decimal]
            invalid_amount: NotRequired[Decimal]
            nullable_amount: NotRequired[Decimal | None]
            label: NotRequired[str]
        ```

    === "msgspec"

        ```python
        # generated by datamodel-codegen:
        #   filename:  serialized_decimal_defaults.json

        from __future__ import annotations

        from decimal import Decimal

        from msgspec import UNSET, Struct, UnsetType


        class Invoice(Struct):
            amount: Decimal | UnsetType = Decimal("12.340")
            invalid_amount: Decimal | UnsetType = "not-a-decimal"
            nullable_amount: Decimal | None | UnsetType = UNSET
            label: str | UnsetType = "retail"
        ```

---

## `--empty-enum-field-name` {#empty-enum-field-name}

Name for empty string enum field values.

The `--empty-enum-field-name` flag configures the code generation behavior.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --empty-enum-field-name empty # (1)!
    ```

    1. :material-arrow-left: `--empty-enum-field-name` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "string",
      "enum": [
        true,
        false,
        "",
        "\n",
        "\r\n",
        "\t",
        "\\x08",
        null,
        "\\"
      ]
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  special_enum.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum

    from pydantic import RootModel


    class ModelEnum(Enum):
        True_ = True
        False_ = False
        empty = ""
        field_ = "\n"
        field__ = "\r\n"
        field__1 = "\t"
        field_x08 = "\\x08"
        field__2 = "\\"


    class Model(RootModel[ModelEnum | None]):
        root: ModelEnum | None = None
    ```

---

## `--extra-fields` {#extra-fields}

Configure how generated models handle extra fields not defined in schema.

The `--extra-fields` flag sets the generated models to allow, forbid, or
ignore extra fields. With `--extra-fields allow`, models will accept and
store fields not defined in the schema. Options: allow, ignore, forbid.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --extra-fields allow # (1)!
    ```

    1. :material-arrow-left: `--extra-fields` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```graphql
    type Person {
        id: ID!
        name: String!
        height: Int
        mass: Int
        hair_color: String
        skin_color: String
        eye_color: String
        birth_year: String
        gender: String
    
        # Relationships
        homeworld_id: ID
        homeworld: Planet
        species: [Species!]!
        species_ids: [ID!]!
        films: [Film!]!
        films_ids: [ID!]!
        starships: [Starship!]!
        starships_ids: [ID!]!
        vehicles: [Vehicle!]!
        vehicles_ids: [ID!]!
    }
    
    type Planet {
        id: ID!
        name: String!
        rotation_period: String
        orbital_period: String
        diameter: String
        climate: String
        gravity: String
        terrain: String
        surface_water: String
        population: String
    
        # Relationships
        residents: [Person!]!
        residents_ids: [ID!]!
        films: [Film!]!
        films_ids: [ID!]!
    }
    
    type Species {
        id: ID!
        name: String!
        classification: String
        designation: String
        average_height: String
        skin_colors: String
        hair_colors: String
        eye_colors: String
        average_lifespan: String
        language: String
    
        # Relationships
        people: [Person!]!
        people_ids: [ID!]!
        films: [Film!]!
        films_ids: [ID!]!
    }
    
    type Vehicle {
        id: ID!
        name: String!
        model: String
        manufacturer: String
        cost_in_credits: String
        length: String
        max_atmosphering_speed: String
        crew: String
        passengers: String
        cargo_capacity: String
        consumables: String
        vehicle_class: String
    
        # Relationships
        pilots: [Person!]!
        pilots_ids: [ID!]!
        films: [Film!]!
        films_ids: [ID!]!
    }
    
    type Starship {
        id: ID!
        name: String!
        model: String
        manufacturer: String
        cost_in_credits: String
        length: String
        max_atmosphering_speed: String
        crew: String
        passengers: String
        cargo_capacity: String
        consumables: String
        hyperdrive_rating: String
        MGLT: String
        starship_class: String
    
        # Relationships
        pilots: [Person!]!
        pilots_ids: [ID!]!
        films: [Film!]!
        films_ids: [ID!]!
    }
    
    type Film {
      id: ID!
      title: String!
      episode_id: Int!
      opening_crawl: String!
      director: String!
      producer: String
      release_date: String!
    
      # Relationships
      characters: [Person!]!
      characters_ids: [ID!]!
      planets: [Planet!]!
      planets_ids: [ID!]!
      starships: [Starship!]!
      starships_ids: [ID!]!
      vehicles: [Vehicle!]!
      vehicles_ids: [ID!]!
      species: [Species!]!
      species_ids: [ID!]!
    }
    
    type Query {
      planet(id: ID!): Planet
      listPlanets(page: Int): [Planet!]!
      person(id: ID!): Person
      listPeople(page: Int): [Person!]!
      species(id: ID!): Species
      listSpecies(page: Int): [Species!]!
      film(id: ID!): Film
      listFilms(page: Int): [Film!]!
      starship(id: ID!): Starship
      listStarships(page: Int): [Starship!]!
      vehicle(id: ID!): Vehicle
      listVehicles(page: Int): [Vehicle!]!
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  simple-star-wars.graphql
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Literal

    from pydantic import BaseModel, ConfigDict, Field
    from typing_extensions import TypeAliasType

    Boolean = TypeAliasType("Boolean", bool)
    """
    The `Boolean` scalar type represents `true` or `false`.
    """


    ID = TypeAliasType("ID", str)
    """
    The `ID` scalar type represents a unique identifier, often used to refetch an object or as key for a cache. The ID type appears in a JSON response as a String; however, it is not intended to be human-readable. When expected as an input type, any string (such as `"4"`) or integer (such as `4`) input value will be accepted as an ID.
    """


    Int = TypeAliasType("Int", int)
    """
    The `Int` scalar type represents non-fractional signed whole numeric values. Int can represent values between -(2^31) and 2^31 - 1.
    """


    String = TypeAliasType("String", str)
    """
    The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
    """


    class Film(BaseModel):
        model_config = ConfigDict(
            extra="allow",
        )
        characters: list[Person]
        characters_ids: list[ID]
        director: String
        episode_id: Int
        id: ID
        opening_crawl: String
        planets: list[Planet]
        planets_ids: list[ID]
        producer: String | None = None
        release_date: String
        species: list[Species]
        species_ids: list[ID]
        starships: list[Starship]
        starships_ids: list[ID]
        title: String
        vehicles: list[Vehicle]
        vehicles_ids: list[ID]
        typename__: Literal["Film"] | None = Field("Film", alias="__typename")


    class Person(BaseModel):
        model_config = ConfigDict(
            extra="allow",
        )
        birth_year: String | None = None
        eye_color: String | None = None
        films: list[Film]
        films_ids: list[ID]
        gender: String | None = None
        hair_color: String | None = None
        height: Int | None = None
        homeworld: Planet | None = None
        homeworld_id: ID | None = None
        id: ID
        mass: Int | None = None
        name: String
        skin_color: String | None = None
        species: list[Species]
        species_ids: list[ID]
        starships: list[Starship]
        starships_ids: list[ID]
        vehicles: list[Vehicle]
        vehicles_ids: list[ID]
        typename__: Literal["Person"] | None = Field("Person", alias="__typename")


    class Planet(BaseModel):
        model_config = ConfigDict(
            extra="allow",
        )
        climate: String | None = None
        diameter: String | None = None
        films: list[Film]
        films_ids: list[ID]
        gravity: String | None = None
        id: ID
        name: String
        orbital_period: String | None = None
        population: String | None = None
        residents: list[Person]
        residents_ids: list[ID]
        rotation_period: String | None = None
        surface_water: String | None = None
        terrain: String | None = None
        typename__: Literal["Planet"] | None = Field("Planet", alias="__typename")


    class Species(BaseModel):
        model_config = ConfigDict(
            extra="allow",
        )
        average_height: String | None = None
        average_lifespan: String | None = None
        classification: String | None = None
        designation: String | None = None
        eye_colors: String | None = None
        films: list[Film]
        films_ids: list[ID]
        hair_colors: String | None = None
        id: ID
        language: String | None = None
        name: String
        people: list[Person]
        people_ids: list[ID]
        skin_colors: String | None = None
        typename__: Literal["Species"] | None = Field("Species", alias="__typename")


    class Starship(BaseModel):
        model_config = ConfigDict(
            extra="allow",
        )
        MGLT: String | None = None
        cargo_capacity: String | None = None
        consumables: String | None = None
        cost_in_credits: String | None = None
        crew: String | None = None
        films: list[Film]
        films_ids: list[ID]
        hyperdrive_rating: String | None = None
        id: ID
        length: String | None = None
        manufacturer: String | None = None
        max_atmosphering_speed: String | None = None
        model: String | None = None
        name: String
        passengers: String | None = None
        pilots: list[Person]
        pilots_ids: list[ID]
        starship_class: String | None = None
        typename__: Literal["Starship"] | None = Field("Starship", alias="__typename")


    class Vehicle(BaseModel):
        model_config = ConfigDict(
            extra="allow",
        )
        cargo_capacity: String | None = None
        consumables: String | None = None
        cost_in_credits: String | None = None
        crew: String | None = None
        films: list[Film]
        films_ids: list[ID]
        id: ID
        length: String | None = None
        manufacturer: String | None = None
        max_atmosphering_speed: String | None = None
        model: String | None = None
        name: String
        passengers: String | None = None
        pilots: list[Person]
        pilots_ids: list[ID]
        vehicle_class: String | None = None
        typename__: Literal["Vehicle"] | None = Field("Vehicle", alias="__typename")


    Film.model_rebuild()
    Person.model_rebuild()
    ```

---

## `--field-constraints` {#field-constraints}

Generate Field() with validation constraints from schema.

The `--field-constraints` flag generates Pydantic Field() definitions with
validation constraints (min/max length, pattern, etc.) from the schema.

**Related:** [`--strict-types`](typing-customization.md#strict-types)

**See also:** [Field Constraints](../field-constraints.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --field-constraints # (1)!
    ```

    1. :material-arrow-left: `--field-constraints` - the option documented here

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

        === "Pydantic v2"

            ```python
            # generated by datamodel-codegen:
            #   filename:  api_constrained.yaml
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from pydantic import AnyUrl, BaseModel, Field, RootModel


            class Pet(BaseModel):
                id: int = Field(..., ge=0, le=9223372036854775807)
                name: str = Field(..., max_length=256)
                tag: str | None = Field(None, max_length=64)


            class Pets(RootModel[list[Pet]]):
                root: list[Pet] = Field(..., max_length=10, min_length=1)


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
                apiKey: str | None = Field(None, description="To be used as a dataset parameter value")
                apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
                apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
                apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")


            class Apis(RootModel[list[Api]]):
                root: list[Api]


            class Event(BaseModel):
                name: str | None = None


            class Result(BaseModel):
                event: Event | None = None
            ```

    === "JSON Schema"

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
            StrictFloat,
            StrictInt,
            StrictStr,
        )


        class User(BaseModel):
            name: StrictStr | None = Field(None, examples=["ken"])
            age: StrictInt | None = None
            salary: StrictInt | None = Field(None, ge=0)
            debt: StrictInt | None = Field(None, le=0)
            loan: StrictFloat | None = Field(None, le=0.0)
            tel: StrictStr | None = Field(None, pattern="^(\\([0-9]{3}\\))?[0-9]{3}-[0-9]{4}$")
            height: StrictFloat | None = Field(None, ge=0.0)
            weight: StrictFloat | None = Field(None, ge=0.0)
            score: StrictFloat | None = Field(None, ge=1e-08)
            active: StrictBool | None = None
            photo: StrictBytes | None = Field(None, min_length=100)
        ```

---

## `--field-extra-keys` {#field-extra-keys}

Include specific extra keys in Field() definitions.

The `--field-extra-keys` flag configures the code generation behavior.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --field-extra-keys key2 --field-extra-keys-without-x-prefix x-repr # (1)!
    ```

    1. :material-arrow-left: `--field-extra-keys` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Extras",
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "description": "normal key",
          "key1": 123,
          "key2": 456,
          "$exclude": 123,
          "invalid-key-1": "abc",
          "-invalid+key_2": "efg",
          "$comment": "comment",
          "$id": "#name",
          "register": "hij",
          "schema": "klm",
          "x-repr": true,
          "x-abc": true,
          "example": "example",
          "readOnly": true
        },
        "age": {
          "type": "integer",
          "example": 12,
          "writeOnly": true,
          "examples": [
            13,
            20
          ]
        },
        "status": {
          "type": "string",
          "examples": [
            "active"
          ]
        }
      }
    }
    ```

    **Output:**

    === "Pydantic v2"

        ```python
        # generated by datamodel-codegen:
        #   filename:  extras.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, Field


        class Extras(BaseModel):
            name: str | None = Field(
                None,
                description="normal key",
                examples=["example"],
                json_schema_extra={"key2": 456, "invalid-key-1": "abc"},
                repr=True,
            )
            age: int | None = Field(None, examples=[13, 20], json_schema_extra={"example": 12})
            status: str | None = Field(None, examples=["active"])
        ```

---

## `--field-extra-keys-without-x-prefix` {#field-extra-keys-without-x-prefix}

Include schema extension keys in Field() without requiring 'x-' prefix.

The --field-extra-keys-without-x-prefix option allows you to specify custom
schema extension keys that should be included in Pydantic Field() extras without
the 'x-' prefix requirement. For example, 'x-repr' in the schema becomes 'repr'
in Field(). This is useful for custom schema extensions and vendor-specific metadata.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --field-include-all-keys --field-extra-keys-without-x-prefix x-repr # (1)!
    ```

    1. :material-arrow-left: `--field-extra-keys-without-x-prefix` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Extras",
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "description": "normal key",
          "key1": 123,
          "key2": 456,
          "$exclude": 123,
          "invalid-key-1": "abc",
          "-invalid+key_2": "efg",
          "$comment": "comment",
          "$id": "#name",
          "register": "hij",
          "schema": "klm",
          "x-repr": true,
          "x-abc": true,
          "example": "example",
          "readOnly": true
        },
        "age": {
          "type": "integer",
          "example": 12,
          "writeOnly": true,
          "examples": [
            13,
            20
          ]
        },
        "status": {
          "type": "string",
          "examples": [
            "active"
          ]
        }
      }
    }
    ```

    **Output:**

    === "Pydantic v2"

        ```python
        # generated by datamodel-codegen:
        #   filename:  extras.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, Field


        class Extras(BaseModel):
            name: str | None = Field(
                None,
                description="normal key",
                examples=["example"],
                json_schema_extra={
                    "key1": 123,
                    "key2": 456,
                    "$exclude": 123,
                    "invalid-key-1": "abc",
                    "-invalid+key_2": "efg",
                    "$comment": "comment",
                    "register": "hij",
                    "schema": "klm",
                    "x-abc": True,
                    "readOnly": True,
                },
                repr=True,
            )
            age: int | None = Field(None, examples=[13, 20], json_schema_extra={"example": 12, "writeOnly": True})
            status: str | None = Field(None, examples=["active"])
        ```

---

## `--field-include-all-keys` {#field-include-all-keys}

Include all schema keys in Field() json_schema_extra.

The `--field-include-all-keys` flag configures the code generation behavior.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --field-include-all-keys # (1)!
    ```

    1. :material-arrow-left: `--field-include-all-keys` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Person",
      "type": "object",
      "properties": {
        "firstName": {
          "type": "string",
          "description": "The person's first name."
        },
        "lastName": {
          "type": ["string", "null"],
          "description": "The person's last name."
        },
        "age": {
          "description": "Age in years which must be equal to or greater than zero.",
          "type": "integer",
          "minimum": 0
        },
        "friends": {
          "type": "array"
        },
        "comment": {
          "type": "null"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  person.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel, Field, conint


    class Person(BaseModel):
        firstName: str | None = Field(None, description="The person's first name.")
        lastName: str | None = Field(None, description="The person's last name.")
        age: conint(ge=0) | None = Field(None, description="Age in years which must be equal to or greater than zero.")
        friends: list[Any] | None = None
        comment: None = Field(None)
    ```

---

## `--field-type-collision-strategy` {#field-type-collision-strategy}

Rename type class instead of field when names collide (Pydantic v2 only).

The `--field-type-collision-strategy` flag controls how field name and type name
collisions are resolved. With `rename-type`, the type class is renamed with a suffix
to preserve the original field name, instead of renaming the field and adding an alias.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type pydantic_v2.BaseModel --field-type-collision-strategy rename-type # (1)!
    ```

    1. :material-arrow-left: `--field-type-collision-strategy` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "title": "Test",
      "type": "object",
      "properties": {
        "TestObject": {
          "title": "TestObject",
          "type": "object",
          "properties": {
            "test_string": {
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
    #   filename:  field_has_same_name.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, Field


    class TestObject_1(BaseModel):
        test_string: str | None = None


    class Test(BaseModel):
        TestObject: TestObject_1 | None = Field(None, title="TestObject")
    ```

---

## `--infer-union-variant-names` {#infer-union-variant-names}

Infer names for inline oneOf/anyOf object variants from literal fields.

The `--infer-union-variant-names` flag uses branch-local `const` values or
single-value enums on a shared discriminator-style property to name generated
inline union models. This is useful when inline union branches would otherwise
receive position-based names such as `Event` and `Event1`.

The literal value can also come from a single-value enum or an internal `$ref`.
Existing generated output is preserved unless this option is enabled.

**Related:** [`--use-title-as-name`](#use-title-as-name)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --infer-union-variant-names # (1)!
    ```

    1. :material-arrow-left: `--infer-union-variant-names` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "WebhookEnvelope",
      "type": "object",
      "required": ["event"],
      "properties": {
        "event": {
          "oneOf": [
            {
              "type": "object",
              "required": ["type", "id"],
              "properties": {
                "type": {"const": "message.created"},
                "id": {"type": "string"}
              }
            },
            {
              "type": "object",
              "required": ["type", "reason"],
              "properties": {
                "type": {"const": "message.failed"},
                "reason": {"type": "string"}
              }
            }
          ]
        }
      }
    }
    ```

    **Output:**

    === "With Option"

        ```python
        # generated by datamodel-codegen:
        #   filename:  infer_union_variant_names.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Literal

        from pydantic import BaseModel


        class EventMessageCreated(BaseModel):
            type: Literal["message.created"]
            id: str


        class EventMessageFailed(BaseModel):
            type: Literal["message.failed"]
            reason: str


        class WebhookEnvelope(BaseModel):
            event: EventMessageCreated | EventMessageFailed
        ```

    === "Without Option"

        ```python
        # generated by datamodel-codegen:
        #   filename:  infer_union_variant_names.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Literal

        from pydantic import BaseModel


        class Event(BaseModel):
            type: Literal["message.created"]
            id: str


        class Event1(BaseModel):
            type: Literal["message.failed"]
            reason: str


        class WebhookEnvelope(BaseModel):
            event: Event | Event1
        ```

---

## `--no-alias` {#no-alias}

Disable Field alias generation for non-Python-safe property names.

The `--no-alias` flag disables automatic alias generation when JSON property
names contain characters invalid in Python (like hyphens). Without this flag,
fields are renamed to Python-safe names with `Field(alias='original-name')`.
With this flag, only Python-safe names are used without aliases.

**See also:** [Field Aliases](../aliases.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --no-alias # (1)!
    ```

    1. :material-arrow-left: `--no-alias` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Person",
      "type": "object",
      "properties": {
        "first-name": {
          "type": "string"
        },
        "last-name": {
          "type": "string"
        },
        "email_address": {
          "type": "string"
        }
      },
      "required": ["first-name", "last-name"]
    }
    ```

    **Output:**

    === "With Option"

        ```python
        # generated by datamodel-codegen:
        #   filename:  no_alias.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel


        class Person(BaseModel):
            first_name: str
            last_name: str
            email_address: str | None = None
        ```

    === "Without Option"

        ```python
        # generated by datamodel-codegen:
        #   filename:  no_alias.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, Field


        class Person(BaseModel):
            first_name: str = Field(..., alias="first-name")
            last_name: str = Field(..., alias="last-name")
            email_address: str | None = None
        ```

---

## `--no-deserialize-default-values` {#no-deserialize-default-values}

Keep serialized schema defaults instead of deserializing selected types.

The `--no-deserialize-default-values` flag explicitly disables default-value deserialization, including when a
preset enables it. Compatibility warnings still identify direct scalar defaults whose serialized form does not
match the generated Python type.

**Related:** [`--deserialize-default-values`](#deserialize-default-values), [`--preset`](base-options.md#preset)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --preset standard-py310-20260826 --no-deserialize-default-values --disable-timestamp --formatters builtin # (1)!
    ```

    1. :material-arrow-left: `--no-deserialize-default-values` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Invoice",
      "type": "object",
      "properties": {
        "amount": {
          "type": "string",
          "format": "decimal",
          "default": "12.340"
        },
        "invalid_amount": {
          "type": "string",
          "format": "decimal",
          "default": "not-a-decimal"
        },
        "nullable_amount": {
          "type": ["string", "null"],
          "format": "decimal",
          "default": null
        },
        "label": {
          "type": "string",
          "default": "retail"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  serialized_decimal_defaults.json

    from __future__ import annotations

    from decimal import Decimal

    from pydantic import BaseModel, ConfigDict


    class Invoice(BaseModel):
        model_config = ConfigDict(
            populate_by_name=True,
        )
        amount: Decimal = "12.340"
        invalid_amount: Decimal = "not-a-decimal"
        nullable_amount: Decimal | None = None
        label: str = "retail"
    ```

---

## `--original-field-name-delimiter` {#original-field-name-delimiter}

Specify delimiter for original field names when using snake-case conversion.

The `--original-field-name-delimiter` option works with `--snake-case-field` to specify
the delimiter used in original field names. This is useful when field names contain
delimiters like spaces or hyphens that should be treated as word boundaries during
snake_case conversion.

**Option relationships:**

- **Requires:** [`--snake-case-field`](field-customization.md#snake-case-field) enabled - `--original-field-name-delimiter` can not be used without `--snake-case-field`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --snake-case-field --original-field-name-delimiter " " # (1)!
    ```

    1. :material-arrow-left: `--original-field-name-delimiter` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "object",
      "properties": {
        "SpaceIF": {
          "$ref": "#/definitions/SpaceIF"
        }
      },
      "definitions": {
        "SpaceIF": {
          "type": "string",
          "enum": [
            "Space Field"
          ]
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  space_field_enum.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum

    from pydantic import BaseModel, Field


    class SpaceIF(Enum):
        space_field = "Space Field"


    class Model(BaseModel):
        space_if: SpaceIF | None = Field(None, alias="SpaceIF")
    ```

---

## `--remove-special-field-name-prefix` {#remove-special-field-name-prefix}

Remove the special prefix from field names.

The `--remove-special-field-name-prefix` flag configures the code generation behavior.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --remove-special-field-name-prefix # (1)!
    ```

    1. :material-arrow-left: `--remove-special-field-name-prefix` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
        "$id": "schema_v2.json",
        "$schema": "http://json-schema.org/schema#",
    
        "type": "object",
        "properties": {
            "@id": {
                "type": "string",
                "format": "uri",
                "pattern": "^http.*$",
                "title": "Id must be presesnt and must be a URI"
            },
            "@type": { "type": "string" },
            "@+!type": { "type": "string" },
            "@-!type": { "type": "string" },
            "profile": { "type": "string" }
        },
        "required": ["@id", "@type"]
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  special_prefix_model.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import AnyUrl, BaseModel, Field


    class Model(BaseModel):
        id: AnyUrl = Field(..., alias="@id", title="Id must be presesnt and must be a URI")
        type: str = Field(..., alias="@type")
        type_1: str | None = Field(None, alias="@+!type")
        type_2: str | None = Field(None, alias="@-!type")
        profile: str | None = None
    ```

---

## `--serialization-aliases` {#serialization-aliases}

Apply custom Pydantic v2 serialization aliases via inline JSON or a JSON file path.

The `--serialization-aliases` option lets Pydantic v2 models keep input aliases
or validation aliases while using separate output-only names for serialization.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --aliases aliases/serialization_aliases.json --serialization-aliases aliases/serialization_aliases_output.json --output-model-type pydantic_v2.BaseModel # (1)!
    ```

    1. :material-arrow-left: `--serialization-aliases` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Messaging",
      "type": "object",
      "definitions": {
        "SendMessageV2": {
          "type": "object",
          "properties": {
            "textMessage": {
              "type": "string"
            }
          },
          "required": ["textMessage"]
        },
        "OtherMessage": {
          "type": "object",
          "properties": {
            "other-name": {
              "type": "string"
            }
          },
          "required": ["other-name"]
        },
        "SameNameMessage": {
          "type": "object",
          "properties": {
            "same-name": {
              "type": "string"
            }
          },
          "required": ["same-name"]
        },
        "RequiredOnlyMessage": {
          "type": "object",
          "allOf": [
            {
              "type": "object",
              "properties": {
                "known-name": {
                  "type": "string"
                }
              },
              "required": [
                "known-name",
                "missing-required",
                "plain-missing",
                "allof-unmapped"
              ]
            }
          ],
          "required": [
            "known-name",
            "missing-required",
            "plain-missing",
            "allof-unmapped"
          ]
        },
        "RequiredOnlyGrandBase": {
          "type": "object",
          "properties": {
            "grand-name": {
              "type": "integer"
            },
            "bare-name": {}
          },
          "required": ["grand-name", "bare-name"]
        },
        "RequiredOnlyBase": {
          "type": "object",
          "properties": {
            "base-name": {
              "type": "string"
            }
          },
          "allOf": [
            {
              "$ref": "#/definitions/RequiredOnlyGrandBase"
            }
          ],
          "required": ["base-name"]
        },
        "RequiredOnlyRefMessage": {
          "type": "object",
          "properties": {
            "own-name": {
              "type": "string"
            }
          },
          "allOf": [
            {
              "$ref": "#/definitions/RequiredOnlyBase"
            }
          ],
          "required": [
            "own-name",
            "grand-name",
            "bare-name",
            "ignored-missing",
            "ref-missing-required",
            "ref-plain-missing"
          ]
        },
        "RequiredOnlyEmptyAllOfBase": {
          "type": "object",
          "allOf": [
            {
              "type": "object"
            }
          ]
        },
        "RequiredOnlyEmptyAllOfMessage": {
          "type": "object",
          "allOf": [
            {
              "$ref": "#/definitions/RequiredOnlyEmptyAllOfBase"
            }
          ],
          "required": ["empty-missing"]
        },
        "RequiredOnlyRecursiveEmptyGrandBase": {
          "type": "object",
          "properties": {
            "unrelated-name": {
              "type": "string"
            }
          }
        },
        "RequiredOnlyRecursiveEmptyBase": {
          "type": "object",
          "allOf": [
            {
              "$ref": "#/definitions/RequiredOnlyRecursiveEmptyGrandBase"
            }
          ]
        },
        "RequiredOnlyRecursiveEmptyMessage": {
          "type": "object",
          "allOf": [
            {
              "$ref": "#/definitions/RequiredOnlyRecursiveEmptyBase"
            }
          ],
          "required": ["recursive-missing"]
        }
      },
      "properties": {
        "send": {
          "$ref": "#/definitions/SendMessageV2"
        },
        "other": {
          "$ref": "#/definitions/OtherMessage"
        },
        "same": {
          "$ref": "#/definitions/SameNameMessage"
        },
        "requiredOnly": {
          "$ref": "#/definitions/RequiredOnlyMessage"
        },
        "requiredOnlyRef": {
          "$ref": "#/definitions/RequiredOnlyRefMessage"
        },
        "requiredOnlyEmptyAllOf": {
          "$ref": "#/definitions/RequiredOnlyEmptyAllOfMessage"
        },
        "requiredOnlyRecursiveEmpty": {
          "$ref": "#/definitions/RequiredOnlyRecursiveEmptyMessage"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  serialization_aliases.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Any

    from pydantic import AliasChoices, BaseModel, Field


    class SendMessageV2(BaseModel):
        message: str = Field(
            ...,
            serialization_alias="messageText",
            validation_alias=AliasChoices("textMessage", "message", "text"),
        )


    class OtherMessage(BaseModel):
        other_name: str = Field(..., alias="other-name", serialization_alias="otherName")


    class SameNameMessage(BaseModel):
        same_name: str = Field(..., alias="same-name", serialization_alias="same_name")


    class RequiredOnlyMessage(BaseModel):
        known_name: str = Field(..., alias="known-name")
        missingRequiredInput: Any = Field(
            ...,
            serialization_alias="missingRequired",
            validation_alias=AliasChoices("missing-required", "missingRequiredInput"),
        )
        plainMissingInput: Any = Field(..., alias="plain-missing", serialization_alias="plainMissing")
        allof_unmapped: Any = Field(..., alias="allof-unmapped")


    class RequiredOnlyGrandBase(BaseModel):
        grand_name: int = Field(..., alias="grand-name")
        bare_name: Any = Field(..., alias="bare-name")


    class RequiredOnlyBase(RequiredOnlyGrandBase):
        base_name: str = Field(..., alias="base-name")


    class RequiredOnlyRefMessage(RequiredOnlyBase):
        own_name: str = Field(..., alias="own-name")
        grand_name: int = Field(
            ...,
            serialization_alias="grandName",
            validation_alias=AliasChoices("grand-name", "grandInput"),
        )
        bare_name: Any = Field(..., alias="bare-name", serialization_alias="bareName")
        ignored_missing: Any = Field(..., alias="ignored-missing")
        refMissingInput: Any = Field(
            ...,
            serialization_alias="refMissingRequired",
            validation_alias=AliasChoices("ref-missing-required", "refMissingInput"),
        )
        refPlainMissingInput: Any = Field(..., alias="ref-plain-missing", serialization_alias="refPlainMissing")


    class RequiredOnlyEmptyAllOfBase(BaseModel):
        pass


    class RequiredOnlyEmptyAllOfMessage(RequiredOnlyEmptyAllOfBase):
        emptyMissingInput: Any = Field(
            ...,
            serialization_alias="emptyMissing",
            validation_alias=AliasChoices("empty-missing", "emptyMissingInput"),
        )


    class RequiredOnlyRecursiveEmptyGrandBase(BaseModel):
        unrelated_name: str | None = Field(None, alias="unrelated-name")


    class RequiredOnlyRecursiveEmptyBase(RequiredOnlyRecursiveEmptyGrandBase):
        pass


    class RequiredOnlyRecursiveEmptyMessage(RequiredOnlyRecursiveEmptyBase):
        recursiveMissingInput: Any = Field(..., alias="recursive-missing", serialization_alias="recursiveMissing")


    class Messaging(BaseModel):
        send: SendMessageV2 | None = None
        other: OtherMessage | None = None
        same: SameNameMessage | None = None
        requiredOnly: RequiredOnlyMessage | None = None
        requiredOnlyRef: RequiredOnlyRefMessage | None = None
        requiredOnlyEmptyAllOf: RequiredOnlyEmptyAllOfMessage | None = None
        requiredOnlyRecursiveEmpty: RequiredOnlyRecursiveEmptyMessage | None = None
    ```

---

## `--set-default-enum-member` {#set-default-enum-member}

Use the legacy flag for deserializing enum defaults.

The `--set-default-enum-member` flag is deprecated. Use `--deserialize-default-values enum` instead.

**Deprecated:** --set-default-enum-member is deprecated. Use --deserialize-default-values enum instead.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --reuse-model --set-default-enum-member # (1)!
    ```

    1. :material-arrow-left: `--set-default-enum-member` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema",
      "title": "User",
      "type": "object",
      "properties": {
        "name": {
          "type": "string"
        },
        "animal": {
          "type": "string",
          "enum": [
            "dog",
            "cat",
            "snake"
          ],
          "default": "dog"
        },
        "pet": {
          "type": "string",
          "enum": [
            "dog",
            "cat",
            "snake"
          ],
          "default": "cat"
        },
        "redistribute": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "static",
              "connected"
            ]
          }
        }
      },
      "definitions": {
        "redistribute": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "static",
              "connected"
            ]
          },
          "description": "Redistribute type for routes."
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  duplicate_enum.json
    #   timestamp: 2019-07-26T00:00:00+00:00

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
        animal: Animal | None = Animal.dog
        pet: Animal | None = Animal.cat
        redistribute: list[RedistributeEnum] | None = None


    class Redistribute(RootModel[list[RedistributeEnum]]):
        root: list[RedistributeEnum] = Field(..., description="Redistribute type for routes.")
    ```

---

## `--snake-case-field` {#snake-case-field}

Convert field names to snake_case format.

The `--snake-case-field` flag converts camelCase or PascalCase field names
to snake_case format in the generated Python code, following Python naming
conventions (PEP 8).

**Related:** [`--capitalize-enum-members`](#capitalize-enum-members)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --snake-case-field # (1)!
    ```

    1. :material-arrow-left: `--snake-case-field` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "InvalidEnum",
      "type": "string",
      "enum": [
        "1 value",
        " space",
        "*- special",
        "schema",
        "MRO",
        "mro"
      ]
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  invalid_enum_name.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum


    class InvalidEnum(Enum):
        field_1_value = "1 value"
        field_space = " space"
        field___special = "*- special"
        schema = "schema"
        mro_1 = "MRO"
        mro_ = "mro"
    ```

---

## `--special-field-name-prefix` {#special-field-name-prefix}

Prefix to add to special field names (like reserved keywords).

The `--special-field-name-prefix` flag configures the code generation behavior.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --special-field-name-prefix special # (1)!
    ```

    1. :material-arrow-left: `--special-field-name-prefix` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "string",
      "enum": [
        true,
        false,
        "",
        "\n",
        "\r\n",
        "\t",
        "\\x08",
        null,
        "\\"
      ]
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  special_enum.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum

    from pydantic import RootModel


    class ModelEnum(Enum):
        True_ = True
        False_ = False
        special_ = ""
        special__1 = "\n"
        special__ = "\r\n"
        special__2 = "\t"
        special_x08 = "\\x08"
        special__3 = "\\"


    class Model(RootModel[ModelEnum | None]):
        root: ModelEnum | None = None
    ```

---

## `--use-attribute-docstrings` {#use-attribute-docstrings}

Generate field descriptions as attribute docstrings instead of Field descriptions.

The `--use-attribute-docstrings` flag places field descriptions in Python docstring
format (PEP 224 attribute docstrings) rather than in Field(..., description=...).
This provides better IDE support for hovering over attributes. Requires
`--use-field-description` to be enabled.

**Related:** [`--use-field-description`](#use-field-description)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type pydantic_v2.BaseModel --use-field-description --use-attribute-docstrings # (1)!
    ```

    1. :material-arrow-left: `--use-attribute-docstrings` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "object",
      "title": "Person",
      "properties": {
        "name": {
          "type": "string",
          "description": "The person's full name"
        },
        "age": {
          "type": "integer",
          "description": "The person's age in years"
        }
      },
      "required": ["name"]
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  use_attribute_docstrings_test.json
    #   timestamp: 1985-10-26T08:21:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, ConfigDict


    class Person(BaseModel):
        model_config = ConfigDict(
            use_attribute_docstrings=True,
        )
        name: str
        """
        The person's full name
        """
        age: int | None = None
        """
        The person's age in years
        """
    ```

---

## `--use-enum-values-in-discriminator` {#use-enum-values-in-discriminator}

Use enum values in discriminator mappings for union types.

The `--use-enum-values-in-discriminator` flag configures the code generation behavior.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-enum-values-in-discriminator --output-model-type pydantic_v2.BaseModel # (1)!
    ```

    1. :material-arrow-left: `--use-enum-values-in-discriminator` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: "3.0.0"
    components:
      schemas:
        Request:
          oneOf:
            - $ref: '#/components/schemas/RequestV1'
            - $ref: '#/components/schemas/RequestV2'
          discriminator:
            propertyName: version
            mapping:
              v1: '#/components/schemas/RequestV1'
              v2: '#/components/schemas/RequestV2'
    
        RequestVersionEnum:
          type: string
          description: this is not included!
          title: no title!
          enum:
            - v1
            - v2
        RequestBase:
          properties:
            version:
              $ref: '#/components/schemas/RequestVersionEnum'
          required:
            - version
    
        RequestV1:
          allOf:
            - $ref: '#/components/schemas/RequestBase'
          properties:
            request_id:
              type: string
              title: test title
              description: there is description
          required:
            - request_id
        RequestV2:
          allOf:
            - $ref: '#/components/schemas/RequestBase'
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  discriminator_enum.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum
    from typing import Literal

    from pydantic import BaseModel, Field, RootModel


    class RequestVersionEnum(Enum):
        v1 = "v1"
        v2 = "v2"


    class RequestBase(BaseModel):
        version: RequestVersionEnum


    class RequestV1(RequestBase):
        request_id: str = Field(..., description="there is description", title="test title")
        version: Literal[RequestVersionEnum.v1]


    class RequestV2(RequestBase):
        version: Literal[RequestVersionEnum.v2]


    class Request(RootModel[RequestV1 | RequestV2]):
        root: RequestV1 | RequestV2 = Field(..., discriminator="version")
    ```

---

## `--use-field-description` {#use-field-description}

Include schema descriptions as Field docstrings.

The `--use-field-description` flag extracts the `description` property from
schema fields and includes them as docstrings or Field descriptions in the
generated models, preserving documentation from the original schema.

**Related:** [`--use-inline-field-description`](#use-inline-field-description), [`--use-schema-description`](#use-schema-description)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-type-alias --use-field-description # (1)!
    ```

    1. :material-arrow-left: `--use-field-description` - the option documented here

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
                  default: 1
                name:
                  type: string
                tag:
                  type: string
            Pets:
              type: array
              items:
                $ref: "#/components/schemas/Pet"
            Users:
              type: array
              items:
                required:
                  - id
                  - name
                properties:
                  id:
                    type: integer
                    format: int64
                  name:
                    type: string
                  tag:
                    type: string
            Id:
              type: string
            Rules:
              type: array
              items:
                type: string
            Error:
              description: "error result.\nNow with multi-line docstrings."
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
                    description: "To be used as a dataset parameter value.\nNow also with multi-line docstrings."
                  apiVersionNumber:
                    type: string
                    description: To be used as a version parameter value
                  apiUrl:
                    type: string
                    format: uri
                    description: "The URL describing the dataset's fields"
                  apiDocumentationUrl:
                    type: string
                    format: uri
                    description: A URL to the API console for each API
            Event:
              type: object
              description: Event object
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
        #   filename:  api_multiline_docstrings.yaml
        #   timestamp: 2022-11-11T00:00:00+00:00

        from __future__ import annotations

        from pydantic import AnyUrl, BaseModel, RootModel


        class Pet(BaseModel):
            id: int
            name: str
            tag: str | None = None


        class Pets(RootModel[list[Pet]]):
            root: list[Pet]


        class User(BaseModel):
            id: int
            name: str
            tag: str | None = None


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
            apiKey: str | None = None
            """
            To be used as a dataset parameter value.
            Now also with multi-line docstrings.
            """
            apiVersionNumber: str | None = None
            """
            To be used as a version parameter value
            """
            apiUrl: AnyUrl | None = None
            """
            The URL describing the dataset's fields
            """
            apiDocumentationUrl: AnyUrl | None = None
            """
            A URL to the API console for each API
            """


        class Apis(RootModel[list[Api]]):
            root: list[Api]


        class Event(BaseModel):
            name: str | None = None


        class Result(BaseModel):
            event: Event | None = None
        ```

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


        AnnotatedType = TypeAliasType("AnnotatedType", Annotated[str | bool, Field(..., title="MyAnnotatedType")])
        """
        An annotated union type
        """


        class ModelWithTypeAliasField(BaseModel):
            simple_field: SimpleString | None = None
            union_field: UnionType | None = None
            array_field: ArrayType | None = None
            annotated_field: AnnotatedType | None = None
        ```

---

## `--use-field-description-example` {#use-field-description-example}

Add field examples to docstrings.

The `--use-field-description-example` flag adds the `example` or `examples`
property from schema fields as docstrings. This provides documentation that
is visible in IDE intellisense.

**Related:** [`--use-field-description`](#use-field-description), [`--use-inline-field-description`](#use-inline-field-description)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-field-description-example # (1)!
    ```

    1. :material-arrow-left: `--use-field-description-example` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Extras",
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "description": "normal key",
          "key1": 123,
          "key2": 456,
          "$exclude": 123,
          "invalid-key-1": "abc",
          "-invalid+key_2": "efg",
          "$comment": "comment",
          "$id": "#name",
          "register": "hij",
          "schema": "klm",
          "x-repr": true,
          "x-abc": true,
          "example": "example",
          "readOnly": true
        },
        "age": {
          "type": "integer",
          "example": 12,
          "writeOnly": true,
          "examples": [
            13,
            20
          ]
        },
        "status": {
          "type": "string",
          "examples": [
            "active"
          ]
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  extras.json
    #   timestamp: 2022-11-11T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, Field


    class Extras(BaseModel):
        name: str | None = Field(None, description="normal key", examples=["example"])
        """
        Example: 'example'
        """
        age: int | None = Field(None, examples=[13, 20], json_schema_extra={"example": 12})
        """
        Examples:
        - 13
        - 20
        """
        status: str | None = Field(None, examples=["active"])
        """
        Example: 'active'
        """
    ```

---

## `--use-inline-field-description` {#use-inline-field-description}

Add field descriptions as inline comments.

The `--use-inline-field-description` flag adds the `description` property from
schema fields as inline comments after each field definition. This provides
documentation without using Field() wrappers.

**Related:** [`--use-field-description`](#use-field-description), [`--use-schema-description`](#use-schema-description)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-inline-field-description # (1)!
    ```

    1. :material-arrow-left: `--use-inline-field-description` - the option documented here

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
                  default: 1
                name:
                  type: string
                tag:
                  type: string
            Pets:
              type: array
              items:
                $ref: "#/components/schemas/Pet"
            Users:
              type: array
              items:
                required:
                  - id
                  - name
                properties:
                  id:
                    type: integer
                    format: int64
                  name:
                    type: string
                  tag:
                    type: string
            Id:
              type: string
            Rules:
              type: array
              items:
                type: string
            Error:
              description: "error result.\nNow with multi-line docstrings."
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
                    description: "To be used as a dataset parameter value.\nNow also with multi-line docstrings."
                  apiVersionNumber:
                    type: string
                    description: To be used as a version parameter value
                  apiUrl:
                    type: string
                    format: uri
                    description: "The URL describing the dataset's fields"
                  apiDocumentationUrl:
                    type: string
                    format: uri
                    description: A URL to the API console for each API
            Event:
              type: object
              description: Event object
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
        #   filename:  api_multiline_docstrings.yaml
        #   timestamp: 2022-11-11T00:00:00+00:00

        from __future__ import annotations

        from pydantic import AnyUrl, BaseModel, Field, RootModel


        class Pet(BaseModel):
            id: int
            name: str
            tag: str | None = None


        class Pets(RootModel[list[Pet]]):
            root: list[Pet]


        class User(BaseModel):
            id: int
            name: str
            tag: str | None = None


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
                None,
                description="To be used as a dataset parameter value.\nNow also with multi-line docstrings.",
            )
            """
            To be used as a dataset parameter value.
            Now also with multi-line docstrings.
            """

            apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
            """To be used as a version parameter value"""

            apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
            """The URL describing the dataset's fields"""

            apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")
            """A URL to the API console for each API"""


        class Apis(RootModel[list[Api]]):
            root: list[Api]


        class Event(BaseModel):
            name: str | None = None


        class Result(BaseModel):
            event: Event | None = None
        ```

    === "JSON Schema"

        **Input Schema:**

        ```json
        {
          "$schema": "http://json-schema.org/draft-07/schema#",
          "title": "MultilineDescriptionWithExample",
          "type": "object",
          "properties": {
            "name": {
              "type": "string",
              "description": "User name.\nThis is a multi-line description.",
              "example": "John Doe"
            }
          }
        }
        ```

        **Output:**

        ```python
        # generated by datamodel-codegen:
        #   filename:  multiline_description_with_example.json
        #   timestamp: 2022-11-11T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, Field


        class MultilineDescriptionWithExample(BaseModel):
            name: str | None = Field(
                None,
                description="User name.\nThis is a multi-line description.",
                examples=["John Doe"],
            )
            """
            User name.
            This is a multi-line description.

            Example: 'John Doe'
            """
        ```

---

## `--use-schema-description` {#use-schema-description}

Use schema description as class docstring.

The `--use-schema-description` flag extracts the `description` property from
schema definitions and adds it as a docstring to the generated class. This is
useful for preserving documentation from your schema in the generated code.

**Related:** [`--use-field-description`](#use-field-description), [`--use-inline-field-description`](#use-inline-field-description)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-schema-description # (1)!
    ```

    1. :material-arrow-left: `--use-schema-description` - the option documented here

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
              default: 1
            name:
              type: string
            tag:
              type: string
        Pets:
          type: array
          items:
            $ref: "#/components/schemas/Pet"
        Users:
          type: array
          items:
            required:
              - id
              - name
            properties:
              id:
                type: integer
                format: int64
              name:
                type: string
              tag:
                type: string
        Id:
          type: string
        Rules:
          type: array
          items:
            type: string
        Error:
          description: "error result.\nNow with multi-line docstrings."
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
                description: "To be used as a dataset parameter value.\nNow also with multi-line docstrings."
              apiVersionNumber:
                type: string
                description: To be used as a version parameter value
              apiUrl:
                type: string
                format: uri
                description: "The URL describing the dataset's fields"
              apiDocumentationUrl:
                type: string
                format: uri
                description: A URL to the API console for each API
        Event:
          type: object
          description: Event object
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
    #   filename:  api_multiline_docstrings.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import AnyUrl, BaseModel, Field, RootModel


    class Pet(BaseModel):
        id: int
        name: str
        tag: str | None = None


    class Pets(RootModel[list[Pet]]):
        root: list[Pet]


    class User(BaseModel):
        id: int
        name: str
        tag: str | None = None


    class Users(RootModel[list[User]]):
        root: list[User]


    class Id(RootModel[str]):
        root: str


    class Rules(RootModel[list[str]]):
        root: list[str]


    class Error(BaseModel):
        """
        error result.
        Now with multi-line docstrings.
        """

        code: int
        message: str


    class Api(BaseModel):
        apiKey: str | None = Field(
            None,
            description="To be used as a dataset parameter value.\nNow also with multi-line docstrings.",
        )
        apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
        apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
        apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")


    class Apis(RootModel[list[Api]]):
        root: list[Api]


    class Event(BaseModel):
        """
        Event object
        """

        name: str | None = None


    class Result(BaseModel):
        event: Event | None = None
    ```

---

## `--use-serialization-alias` {#use-serialization-alias}

Use serialization_alias instead of alias for field aliasing (Pydantic v2 only).

The `--use-serialization-alias` flag changes field aliasing to use `serialization_alias`
instead of `alias`. This allows setting values using the Pythonic field name while
serializing to the original JSON property name.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-serialization-alias --output-model-type pydantic_v2.BaseModel # (1)!
    ```

    1. :material-arrow-left: `--use-serialization-alias` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Person",
      "type": "object",
      "properties": {
        "first-name": {
          "type": "string"
        },
        "last-name": {
          "type": "string"
        },
        "email_address": {
          "type": "string"
        }
      },
      "required": ["first-name", "last-name"]
    }
    ```

    **Output:**

    === "With Option"

        ```python
        # generated by datamodel-codegen:
        #   filename:  no_alias.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, Field


        class Person(BaseModel):
            first_name: str = Field(..., serialization_alias="first-name")
            last_name: str = Field(..., serialization_alias="last-name")
            email_address: str | None = None
        ```

    === "Without Option"

        ```python
        # generated by datamodel-codegen:
        #   filename:  no_alias.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, Field


        class Person(BaseModel):
            first_name: str = Field(..., alias="first-name")
            last_name: str = Field(..., alias="last-name")
            email_address: str | None = None
        ```

---

## `--use-single-line-docstring` {#use-single-line-docstring}

Emit short docstrings on a single line.

The `--use-single-line-docstring` flag formats docstrings that fit on one line
as compact single-line docstrings while keeping the historical multi-line
format as the default.

**Related:** [`--use-field-description`](#use-field-description), [`--use-schema-description`](#use-schema-description)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-field-description --use-single-line-docstring # (1)!
    ```

    1. :material-arrow-left: `--use-single-line-docstring` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Person",
      "type": "object",
      "properties": {
        "firstName": {
          "type": "string",
          "description": "The person's first name."
        },
        "lastName": {
          "type": ["string", "null"],
          "description": "The person's last name."
        },
        "age": {
          "description": "Age in years which must be equal to or greater than zero.",
          "type": "integer",
          "minimum": 0
        },
        "friends": {
          "type": "array"
        },
        "comment": {
          "type": "null"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  person.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel, Field, conint


    class Person(BaseModel):
        firstName: str | None = None
        """The person's first name."""
        lastName: str | None = None
        """The person's last name."""
        age: conint(ge=0) | None = None
        """Age in years which must be equal to or greater than zero."""
        friends: list[Any] | None = None
        comment: None = Field(None)
    ```

---

## `--use-title-as-name` {#use-title-as-name}

Use schema title as the generated class name.

The `--use-title-as-name` flag uses the `title` property from the schema
as the class name instead of deriving it from the property name or path.
This is useful when schemas have descriptive titles that should be preserved.

**Related:** [`--class-name`](model-customization.md#class-name)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-title-as-name # (1)!
    ```

    1. :material-arrow-left: `--use-title-as-name` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "definitions": {
        "ProcessingStatus": {
          "title": "Processing Status Title",
          "enum": [
            "COMPLETED",
            "PENDING",
            "FAILED"
          ],
          "type": "string",
          "description": "The processing status"
        },
        "kind": {
          "type": "string"
        },
        "ExtendedProcessingTask": {
          "title": "Extended Processing Task Title",
          "oneOf": [
            {
              "$ref": "#"
            },
            {
              "type": "object",
              "title": "NestedCommentTitle",
              "properties": {
                "comment": {
                  "type": "string"
                }
              }
            }
          ]
        },
        "ExtendedProcessingTasks": {
          "title": "Extended Processing Tasks Title",
          "type": "array",
          "items": [
            {
              "$ref": "#/definitions/ExtendedProcessingTask"
            }
          ]
        },
        "ProcessingTask": {
          "title": "Processing Task Title",
          "type": "object",
          "properties": {
            "processing_status_union": {
              "title": "Processing Status Union Title",
              "oneOf": [
                {
                  "title": "Processing Status Detail",
                  "type": "object",
                  "properties": {
                    "id": {
                      "type": "integer"
                    },
                    "description": {
                      "type": "string"
                    }
                  }
                },
                {
                  "$ref": "#/definitions/ExtendedProcessingTask"
                },
                {
                  "$ref": "#/definitions/ProcessingStatus"
                }
              ],
              "default": "COMPLETED"
            },
            "processing_status": {
              "$ref": "#/definitions/ProcessingStatus",
              "default": "COMPLETED"
            },
            "name": {
              "type": "string"
            },
            "kind": {
              "$ref": "#/definitions/kind"
            }
          }
        }
      },
      "title": "Processing Tasks Title",
      "type": "array",
          "items": [
            {
              "$ref": "#/definitions/ProcessingTask"
            }
          ]
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  titles.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum

    from pydantic import BaseModel, Field, RootModel


    class ProcessingStatusTitle(Enum):
        COMPLETED = "COMPLETED"
        PENDING = "PENDING"
        FAILED = "FAILED"


    class Kind(RootModel[str]):
        root: str


    class NestedCommentTitle(BaseModel):
        comment: str | None = None


    class ProcessingStatusDetail(BaseModel):
        id: int | None = None
        description: str | None = None


    class ProcessingTaskTitle(BaseModel):
        processing_status_union: ProcessingStatusUnionTitle | None = Field(
            "COMPLETED", title="Processing Status Union Title", validate_default=True
        )
        processing_status: ProcessingStatusTitle | None = "COMPLETED"
        name: str | None = None
        kind: Kind | None = None


    class ProcessingTasksTitle(RootModel[list[ProcessingTaskTitle]]):
        root: list[ProcessingTaskTitle] = Field(..., title="Processing Tasks Title")


    class ExtendedProcessingTask(RootModel[ProcessingTasksTitle | NestedCommentTitle]):
        root: ProcessingTasksTitle | NestedCommentTitle = Field(..., title="Extended Processing Task Title")


    class ExtendedProcessingTasksTitle(RootModel[list[ExtendedProcessingTask]]):
        root: list[ExtendedProcessingTask] = Field(..., title="Extended Processing Tasks Title")


    class ProcessingStatusUnionTitle(RootModel[ProcessingStatusDetail | ExtendedProcessingTask | ProcessingStatusTitle]):
        root: ProcessingStatusDetail | ExtendedProcessingTask | ProcessingStatusTitle = Field(
            "COMPLETED", title="Processing Status Union Title", validate_default=True
        )


    ProcessingTaskTitle.model_rebuild()
    ```

---
