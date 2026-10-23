# OpenAPI

Handle OpenAPI operation naming, path selection, scopes, and readOnly/writeOnly behavior.

Options are grouped from shared CLI metadata and link back to their generated reference sections.

## Groups

| Group | Options | Description |
|-------|---------|-------------|
| [OpenAPI Naming](#openapi-naming) | 2 | Operation and response model naming. |
| [OpenAPI Paths](#openapi-paths) | 3 | Path selection and path parameter output. |
| [OpenAPI Scopes](#openapi-scopes) | 2 | OpenAPI generation scopes. |
| [Read Only Write Only](#read-only-write-only) | 3 | readOnly/writeOnly model behavior. |

## OpenAPI Naming {#openapi-naming}

Operation and response model naming.

| Option | Description |
|--------|-------------|
| [`--use-operation-id-as-name`](../openapi-only-options.md#use-operation-id-as-name) | Use OpenAPI operationId as the generated function/class name. |
| [`--use-status-code-in-response-name`](../openapi-only-options.md#use-status-code-in-response-name) | Include HTTP status code in response model names. |

## OpenAPI Paths {#openapi-paths}

Path selection and path parameter output.

| Option | Description |
|--------|-------------|
| [`--include-path-parameters`](../openapi-only-options.md#include-path-parameters) | Include OpenAPI path parameters in generated parameter models. |
| [`--openapi-include-paths`](../openapi-only-options.md#openapi-include-paths) | Filter OpenAPI paths to include in model generation. |
| [`--openapi-paths-bodies-as-definitions`](../openapi-only-options.md#openapi-paths-bodies-as-definitions) | Give the bodies of the paths and webhooks scopes the models their schema definit... |

## OpenAPI Scopes {#openapi-scopes}

OpenAPI generation scopes.

| Option | Description |
|--------|-------------|
| [`--openapi-ref-components-as-definitions`](../openapi-only-options.md#openapi-ref-components-as-definitions) | Give a `$ref` component with sibling keywords to a recursive schema its own mode... |
| [`--openapi-scopes`](../openapi-only-options.md#openapi-scopes) | Specify OpenAPI scopes to generate (schemas, paths, parameters). |

## Read Only Write Only {#read-only-write-only}

readOnly/writeOnly model behavior.

| Option | Description |
|--------|-------------|
| [`--dedupe-request-response-variants`](../openapi-only-options.md#dedupe-request-response-variants) | Decide Request/Response variants from the schema graph and build each inline mod... |
| [`--read-only-write-only-model-type`](../openapi-only-options.md#read-only-write-only-model-type) | Generate separate request and response models for readOnly/writeOnly fields. |
| [`--use-frozen-field`](../model-customization.md#use-frozen-field) | Generate frozen (immutable) field definitions for readOnly properties. |
