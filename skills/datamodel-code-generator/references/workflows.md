# Workflows

## Contents

1. OpenAPI to Pydantic v2
2. OpenAPI to a FastAPI server or Python client (experimental)
3. AsyncAPI to Pydantic v2
4. JSON Schema to Pydantic v2
5. JSON Schema split across files
6. Raw JSON sample to models
7. Raw YAML sample to models
8. CSV sample to models
9. GraphQL SDL to models
10. Apache Avro schema to models
11. XML Schema to models
12. Protocol Buffers to models
13. MCP tool schemas to models
14. Remote URL input
15. Existing Python model or dict schema object
16. Multiple modules for large specs
17. Output as dataclasses, TypedDict, or msgspec

## OpenAPI to Pydantic v2

Use this for OpenAPI schemas when the user wants validation models or API payload types.

```bash
uvx datamodel-codegen \
  --input openapi.yaml \
  --input-file-type openapi \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

Common flags: `--read-only-write-only-model-type request-response`, `--openapi-scopes`, `--use-operation-id-as-name`, `--use-status-code-in-response-name`.

Verify with:

```bash
python -c "import pathlib, importlib.util; p=pathlib.Path('models.py'); s=importlib.util.spec_from_file_location('generated_models', p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('import ok')"
```

Expect Pydantic v2 model classes for schema components, and optionally request, response, parameter, or path models when OpenAPI scopes include them.

## OpenAPI to a FastAPI server or Python client (experimental)

Use this when the user wants a FastAPI server or an HTTPX2 client generated from an OpenAPI document, on top of the models.

```bash
uvx datamodel-codegen \
  --input openapi.yaml \
  --input-file-type openapi \
  --openapi-scopes schemas api \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 \
  --output app/models.py \
  --generate-server fastapi \
  --server-output app/server \
  --server-package app.server \
  --server-model-package app.models
```

Generation overwrites the models and the server package on every run: never edit them. Write the implementation of each router group in `app/services/<group>.py`, as class `<Group>` subclassing the `<Group>Service` Protocol of `app.server.services`; with `--server-layout single`, write `app/services/service.py` with class `Operations` subclassing the `Service` Protocol. A secured document adds `app/services/authorize.py` with `authorize`. Generation writes each missing module once with stubs and never overwrites it. Give each server package a parent package of its own. `app/server/main.py` imports them and defines `app`. The run prints the `uv add` command for the runtime dependencies and the next steps; `--output-format json` reports them under `target`.

For a client, use `--generate-client httpx2 --client-output app/client --client-package app.client --client-model-package app.models` instead, and call it as `with Client(base_url=...) as client: client.<group>.<method>(...)`.

Verify with:

```bash
uv add uvicorn
uv run uvicorn app.server.main:app
```

Expect the README of the generated package to list the operations and, under Next steps, the modules to implement.

## AsyncAPI to Pydantic v2

Use this for AsyncAPI message schemas.

```bash
uvx datamodel-codegen \
  --input asyncapi.yaml \
  --input-file-type asyncapi \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

This generates data models from schema-bearing fields. It does not generate producers, consumers, clients, servers, brokers, or runtime protocol adapters.

Verify with:

```bash
python -c "import pathlib, importlib.util; p=pathlib.Path('models.py'); s=importlib.util.spec_from_file_location('generated_models', p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('import ok')"
```

Expect models for payload schemas and reusable schema components.

## JSON Schema to Pydantic v2

Use this for JSON Schema files or JSON Schema written as YAML.

```bash
uvx datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

Common flags: `--schema-version`, `--schema-version-mode`, `--class-name`, `--use-title-as-name`.

Verify with:

```bash
python -c "import pathlib, importlib.util; p=pathlib.Path('models.py'); s=importlib.util.spec_from_file_location('generated_models', p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('import ok')"
```

Expect a root class and nested classes for object definitions.

## JSON Schema split across files

Use this when schemas are stored in a directory or reference each other across files.

```bash
uvx datamodel-codegen \
  --input schemas/ \
  --input-file-type jsonschema \
  --output models/ \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

Common flags: `--external-ref-mapping`, `--reuse-model`, `--reuse-scope`, `--shared-module-name`, `--all-exports-scope`.

Verify with:

```bash
python -c "import models; print('import ok')"
```

Expect a Python package with one or more generated modules and package exports when export flags are enabled.

## Raw JSON sample to models

Use this when the input is example JSON data and not a schema.

```bash
uvx datamodel-codegen \
  --input sample.json \
  --input-file-type json \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

Types are inferred from the sample. The output is only as representative as the sample data. Review optionality, unions, numeric types, and missing fields.

Verify with the single-file import command. Expect a root model class and nested model classes inferred from sample keys.

## Raw YAML sample to models

Use this when the input is example YAML data and not a schema.

```bash
uvx datamodel-codegen \
  --input sample.yaml \
  --input-file-type yaml \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

Use `yaml` only for raw YAML data. If the YAML file is an OpenAPI document, AsyncAPI document, or JSON Schema written as YAML, use `openapi`, `asyncapi`, or `jsonschema`.

Verify with the single-file import command. Expect model classes inferred from YAML mapping keys.

## CSV sample to models

Use this when rows in a CSV sample should define a model shape.

```bash
uvx datamodel-codegen \
  --input sample.csv \
  --input-file-type csv \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

CSV inference depends on available rows and values.

Verify with the single-file import command. Expect one model class with fields from the CSV header.

## GraphQL SDL to models

Use this for GraphQL schema definition language.

```bash
uvx --from 'datamodel-code-generator[graphql]' datamodel-codegen \
  --input schema.graphql \
  --input-file-type graphql \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

Common flags: `--graphql-no-typename`, `--type-mappings`, `--field-extra-keys`.

Verify with the single-file import command. Expect Pydantic models for GraphQL object and input types.

## Apache Avro schema to models

Use this for Avro `.avsc` schema files.

```bash
uvx datamodel-codegen \
  --input schema.avsc \
  --input-file-type avro \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

Avro support generates Python models from schemas. It does not provide Avro runtime encoding or decoding.

Verify with the single-file import command. Expect model classes for Avro records, enums, fixed values, maps, arrays, and unions when present in the schema.

## XML Schema to models

Use this for XSD documents.

```bash
uvx datamodel-codegen \
  --input schema.xsd \
  --input-file-type xmlschema \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

Common flags: `--schema-version`, `--schema-version-mode`, `--class-name`, `--use-title-as-name`.

Verify with the single-file import command. Expect model classes for XML elements and complex types. The CLI does not replace an XML parser or full XML instance validator.

## Protocol Buffers to models

Use this for `.proto` schemas when the user wants Python data models, not generated protobuf runtime classes or gRPC stubs.

```bash
uvx --from 'datamodel-code-generator[protobuf]' datamodel-codegen \
  --input schema.proto \
  --input-file-type protobuf \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

Common flags: `--schema-version`, `--schema-version-mode`, `--class-name`, `--type-mappings`.

Verify with the single-file import command. Expect model classes for messages and enums. Use protobuf tooling separately for serialization, descriptors, and gRPC code.

## MCP tool schemas to models

Use this for MCP tool schema exports containing `inputSchema` and optional `outputSchema` objects.

```bash
uvx datamodel-codegen \
  --input mcp-tools.json \
  --input-file-type mcp-tools \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

MCP tool schemas are converted into JSON Schema definitions before normal model generation.

Verify with the single-file import command. Expect input and output model classes named from tool names and schema roles.

## Remote URL input

Use this for specs fetched by URL or specs that contain remote refs.

```bash
uvx --from 'datamodel-code-generator[http]' datamodel-codegen \
  --url https://example.com/openapi.yaml \
  --input-file-type openapi \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

Common flags: `--http-headers`, `--http-query-parameters`, `--http-timeout`, `--http-local-ref-path`, `--allow-remote-refs`.

Use `--http-ignore-tls` only in trusted development or testing environments.

Verify with the single-file import command. Expect the same shape as local file input after the URL is fetched.

## Existing Python model or dict schema object

Use this to retarget existing Python models or schema dictionaries.

```bash
uvx datamodel-codegen \
  --input-model ./models.py:User \
  --output user_model.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

For dict schema objects:

```bash
uvx datamodel-codegen \
  --input-model ./schemas.py:USER_SCHEMA \
  --input-file-type jsonschema \
  --output user_model.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12
```

Common flag: `--input-model-ref-strategy`.

Verify with the single-file import command. Expect models generated from the Python object's schema.

## Multiple modules for large specs

Use this when one file is too large or the project wants a package.

```bash
uvx datamodel-codegen \
  --input openapi.yaml \
  --input-file-type openapi \
  --output models/ \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 \
  --all-exports-scope recursive
```

Common flags: `--all-exports-scope`, `--all-exports-collision-strategy`, `--treat-dot-as-module`,
`--strict-dotted-module-names`, `--reuse-model`, `--reuse-scope`.

Verify with:

```bash
python -c "import models; print('import ok')"
```

Expect a package with generated modules and `__all__` exports.

## Output as dataclasses, TypedDict, or msgspec

Use these when the user wants non-Pydantic output.

```bash
uvx datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output models.py \
  --output-model-type dataclasses.dataclass \
  --target-python-version 3.12
```

```bash
uvx datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output types.py \
  --output-model-type typing.TypedDict \
  --target-python-version 3.12
```

```bash
uvx --with msgspec datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output structs.py \
  --output-model-type msgspec.Struct \
  --target-python-version 3.12
```

Common flags: `--frozen-dataclasses`, `--keyword-only`, `--strict-nullable`, `--use-frozen-field`.

Verify with the single-file import command. Expect plain dataclasses, TypedDict classes, or msgspec structs instead of Pydantic models.
