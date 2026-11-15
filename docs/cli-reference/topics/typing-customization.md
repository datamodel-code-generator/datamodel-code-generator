# Typing Customization

Control Python annotation syntax, collection types, imports, and type mappings.

Options are grouped from shared CLI metadata and link back to their generated reference sections.

## Groups

| Group | Options | Description |
|-------|---------|-------------|
| [Imports](#imports) | 2 | Generated imports and type-checking import behavior. |
| [Collection Types](#collection-types) | 8 | Collection and tuple/set generation. |
| [Type Alias](#type-alias) | 4 | TypeAlias and root-model alias output. |
| [Type Mapping](#type-mapping) | 22 | Scalar, date/time, and custom type mapping. |
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
| [`--use-tuple-for-closed-arrays`](../typing-customization.md#use-tuple-for-closed-arrays) | Generate tuple types for positional arrays that no other item can follow. |
| [`--use-tuple-for-fixed-items`](../typing-customization.md#use-tuple-for-fixed-items) | Generate tuple types for arrays with items array syntax. |
| [`--use-tuple-for-fixed-length-arrays`](../typing-customization.md#use-tuple-for-fixed-length-arrays) | Generate tuple types for homogeneous fixed-length arrays. |
| [`--use-unique-items-as-set`](../typing-customization.md#use-unique-items-as-set) | Generate set types for arrays with uniqueItems constraint. |
| [`--use-unique-items-as-set-by-value`](../typing-customization.md#use-unique-items-as-set-by-value) | Write a uniqueItems set only where its final items hash by value. |

## Type Alias {#type-alias}

TypeAlias and root-model alias output.

| Option | Description |
|--------|-------------|
| [`--use-plain-alias-for-recursive-models`](../typing-customization.md#use-plain-alias-for-recursive-models) | Write the type alias of a model that names itself as a plain alias in Pydantic o... |
| [`--use-root-model-type-alias`](../typing-customization.md#use-root-model-type-alias) | Generate RootModel as type alias format for better mypy support. |
| [`--use-type-alias`](../typing-customization.md#use-type-alias) | Use TypeAlias instead of root models for type definitions (experimental). |
| [`--use-type-alias-type`](../typing-customization.md#use-type-alias-type) | Use runtime TypeAliasType objects for aliases before Python 3.12 (experimental). |

## Type Mapping {#type-mapping}

Scalar, date/time, and custom type mapping.

| Option | Description |
|--------|-------------|
| [`--output-date-class`](../typing-customization.md#output-date-class) | Specify date class type for date schema fields. |
| [`--output-datetime-class`](../typing-customization.md#output-datetime-class) | Specify datetime class type for date-time schema fields. |
| [`--read-format-as-annotation`](../typing-customization.md#read-format-as-annotation) | Read the format of a schema as an annotation, as JSON Schema does. |
| [`--read-kubernetes-extensions`](../typing-customization.md#read-kubernetes-extensions) | Read the Kubernetes schema extensions as the keywords they stand for. |
| [`--read-unevaluated-properties`](../typing-customization.md#read-unevaluated-properties) | Read a schema-valued `unevaluatedProperties` as `additionalProperties`. |
| [`--strict-types`](../typing-customization.md#strict-types) | Enable strict type validation for specified Python types. |
| [`--type-mappings`](../typing-customization.md#type-mappings) | Override default type mappings for schema formats. |
| [`--type-overrides`](../typing-customization.md#type-overrides) | Replace schema model types with custom Python types via JSON mapping. |
| [`--type-overrides-replace-whole-type`](../typing-customization.md#type-overrides-replace-whole-type) | Make every `--type-overrides` value of a field replace the field's whole type. |
| [`--use-bytes-for-content-media-type`](../typing-customization.md#use-bytes-for-content-media-type) | Type an OpenAPI string with a `contentMediaType` and no `contentEncoding` as `by... |
| [`--use-decimal-for-multiple-of`](../typing-customization.md#use-decimal-for-multiple-of) | Generate Decimal types for fields with multipleOf constraint. |
| [`--use-decimal-in-field-forms`](../typing-customization.md#use-decimal-in-field-forms) | Write `Decimal` for a number with `multipleOf` in the field forms too. |
| [`--use-encoded-str-for-constrained-base64`](../typing-customization.md#use-encoded-str-for-constrained-base64) | Check the constraints of a `format: byte` string on its base64 text in the field... |
| [`--use-object-type`](../typing-customization.md#use-object-type) | Use object instead of Any for unspecified object and array values. |
| [`--use-pendulum`](../typing-customization.md#use-pendulum) | Use pendulum types for date, time, and duration fields. |
| [`--use-single-dict-for-object-maps`](../typing-customization.md#use-single-dict-for-object-maps) | Type an object map as one dict instead of a union of dicts. |
| [`--use-standard-primitive-types`](../typing-customization.md#use-standard-primitive-types) | Use Python standard library types for string formats instead of str. |
| [`--use-strict-str-for-base64`](../typing-customization.md#use-strict-str-for-base64) | Make a `format: byte` string strict under `--strict-types str`. |
| [`--use-typed-map-values-beside-other-types`](../typing-customization.md#use-typed-map-values-beside-other-types) | Type the map values of type arrays that list object beside other types. |
| [`--use-typed-nullable-map-values`](../typing-customization.md#use-typed-nullable-map-values) | Type the values of nullable maps as their schemas say instead of Any. |
| [`--use-x-python-import-for-every-ref`](../typing-customization.md#use-x-python-import-for-every-ref) | Type every reference to a schema with `x-python-import` by the class it imports. |
| [`--use-x-python-type-as-written`](../typing-customization.md#use-x-python-type-as-written) | Write a field's `x-python-type` as given: unions of every spelling and container... |

## Type Syntax {#type-syntax}

Modern annotation syntax and Annotated usage.

| Option | Description |
|--------|-------------|
| [`--no-use-union-operator`](../typing-customization.md#no-use-union-operator) | Use Union\[X, Y\] / Optional\[X\] instead of X \| Y union operator. |
| [`--use-annotated`](../typing-customization.md#use-annotated) | Use typing.Annotated for Field() with constraints. |
| [`--use-union-operator`](../typing-customization.md#use-union-operator) | Use \| operator for Union types (PEP 604). |
