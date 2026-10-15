# Typing Customization

Control Python annotation syntax, collection types, imports, and type mappings.

Options are grouped from shared CLI metadata and link back to their generated reference sections.

## Groups

| Group | Options | Description |
|-------|---------|-------------|
| [Imports](#imports) | 2 | Generated imports and type-checking import behavior. |
| [Collection Types](#collection-types) | 6 | Collection and tuple/set generation. |
| [Type Alias](#type-alias) | 3 | TypeAlias and root-model alias output. |
| [Type Mapping](#type-mapping) | 10 | Scalar, date/time, and custom type mapping. |
| [Type Syntax](#type-syntax) | 3 | Modern annotation syntax and Annotated usage. |

## Imports {#imports}

Generated imports and type-checking import behavior.

| Option | Description |
|--------|-------------|
| [`--disable-future-imports`](../typing-customization.md#disable-future-imports) | Prevent automatic addition of __future__ imports in generated code. |
| [`--import-overrides`](../typing-customization.md#import-overrides) | Override modules for generated imports by symbol name. |

## Collection Types {#collection-types}

Collection and tuple/set generation.

| Option | Description |
|--------|-------------|
| [`--no-use-standard-collections`](../typing-customization.md#no-use-standard-collections) | Use typing.Dict/List instead of built-in dict/list for container types. |
| [`--use-generic-container-types`](../typing-customization.md#use-generic-container-types) | Use generic container types (Sequence, Mapping) for type hinting. |
| [`--use-standard-collections`](../typing-customization.md#use-standard-collections) | Use built-in dict/list instead of typing.Dict/List. |
| [`--use-tuple-for-fixed-items`](../typing-customization.md#use-tuple-for-fixed-items) | Generate tuple types for arrays with items array syntax. |
| [`--use-tuple-for-fixed-length-arrays`](../typing-customization.md#use-tuple-for-fixed-length-arrays) | Generate tuple types for homogeneous fixed-length arrays. |
| [`--use-unique-items-as-set`](../typing-customization.md#use-unique-items-as-set) | Generate set types for arrays with uniqueItems constraint. |

## Type Alias {#type-alias}

TypeAlias and root-model alias output.

| Option | Description |
|--------|-------------|
| [`--use-root-model-type-alias`](../typing-customization.md#use-root-model-type-alias) | Generate RootModel as type alias format for better mypy support. |
| [`--use-type-alias`](../typing-customization.md#use-type-alias) | Use TypeAlias instead of root models for type definitions (experimental). |
| [`--use-type-alias-type`](../typing-customization.md#use-type-alias-type) | Use runtime TypeAliasType objects for aliases before Python 3.12 (experimental). |

## Type Mapping {#type-mapping}

Scalar, date/time, and custom type mapping.

| Option | Description |
|--------|-------------|
| [`--output-date-class`](../typing-customization.md#output-date-class) | Specify date class type for date schema fields. |
| [`--output-datetime-class`](../typing-customization.md#output-datetime-class) | Specify datetime class type for date-time schema fields. |
| [`--strict-types`](../typing-customization.md#strict-types) | Enable strict type validation for specified Python types. |
| [`--type-mappings`](../typing-customization.md#type-mappings) | Override default type mappings for schema formats. |
| [`--type-overrides`](../typing-customization.md#type-overrides) | Replace schema model types with custom Python types via JSON mapping. |
| [`--use-decimal-for-multiple-of`](../typing-customization.md#use-decimal-for-multiple-of) | Generate Decimal types for fields with multipleOf constraint. |
| [`--use-object-type`](../typing-customization.md#use-object-type) | Use object instead of Any for unspecified object and array values. |
| [`--use-pendulum`](../typing-customization.md#use-pendulum) | Use pendulum types for date, time, and duration fields. |
| [`--use-standard-primitive-types`](../typing-customization.md#use-standard-primitive-types) | Use Python standard library types for string formats instead of str. |
| [`--use-typed-nullable-map-values`](../typing-customization.md#use-typed-nullable-map-values) | Type the values of nullable maps as their schemas say instead of Any. |

## Type Syntax {#type-syntax}

Modern annotation syntax and Annotated usage.

| Option | Description |
|--------|-------------|
| [`--no-use-union-operator`](../typing-customization.md#no-use-union-operator) | Use Union\[X, Y\] / Optional\[X\] instead of X \| Y union operator. |
| [`--use-annotated`](../typing-customization.md#use-annotated) | Use typing.Annotated for Field() with constraints. |
| [`--use-union-operator`](../typing-customization.md#use-union-operator) | Use \| operator for Union types (PEP 604). |
