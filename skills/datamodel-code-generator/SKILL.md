---
name: datamodel-code-generator
description: >
  Use this skill when the user wants Python data models, Pydantic models,
  dataclasses, TypedDicts, msgspec structs, or type-safe Python classes
  generated from OpenAPI, AsyncAPI, JSON Schema, GraphQL, JSON/YAML/CSV
  sample data, MCP tool schemas, Protocol Buffers, XML Schema, Apache Avro,
  or existing Python model objects. Use it for requests such as "Pydantic
  models from this schema", "types for this API", "parse this JSON into
  classes", or "convert this spec into Python models". Prefer the
  datamodel-codegen CLI over hand-written model code whenever an input
  schema, API spec, sample data file, or Python model object exists.
license: MIT
metadata:
  status: experimental
---

# datamodel-code-generator

## Why use this CLI

Use `datamodel-codegen` whenever a schema, API spec, sample data file, or existing Python model object exists.

The CLI handles refs, enums, nested models, unions, `allOf`, `oneOf`, `anyOf`, and model reuse better than hand-written code.

Do not hand-write generated models first.

## Availability check

Check one-shot execution first:

```bash
uvx datamodel-codegen --help
```

If `uvx` is unavailable:

```bash
pipx run datamodel-code-generator --help
```

If the project already uses `uv` and project-scoped execution is preferred:

```bash
uv run --with datamodel-code-generator datamodel-codegen --help
```

If the project already has a managed Python virtual environment and the user approves project dependency changes:

```bash
python -m pip install datamodel-code-generator
datamodel-codegen --help
```

Prefer one-shot execution. Do not add or upgrade datamodel-code-generator in the user's project without explicit approval.

If offline and no installed command is available, say that the CLI cannot be run. Only then fall back to hand-written models.

## Option discovery for agents

Before choosing options, run the recommender (experimental) and apply its result:

```bash
datamodel-codegen --input SCHEMA [--input-file-type TYPE] [--output-model-type TYPE] \
  --recommend-options --output-format json
```

Use `arguments` from the payload (or `config` for a project configured in `pyproject.toml`) as the starting command. Do not add options it lists under `not_recommended` without a stated reason.

When the user's goal needs judgment beyond the schema's shape, use the CLI's own LLM workflow and add `--recommend-options` to it, so the prompt includes the recommendation; decide from both:

```bash
datamodel-codegen [known options] --generate-prompt "Find the minimal options for this model-generation task." \
  --recommend-options
```

Include the options already known from the project or user request. The prompt output explains current options, available options, option relationships, and verification steps. Without an input schema, run `--generate-prompt` alone.

For agent tooling that consumes structured data, prefer JSON:

```bash
datamodel-codegen [known options] \
  --generate-prompt "Find the minimal options for this model-generation task." \
  --output-format json
```

When defining or validating tool contracts, fetch the schema separately:

```bash
datamodel-codegen --output-format-json-schema structured-output
```

Use `generate-prompt` when the consumer only handles prompt payloads, `generation` when it only handles generated-file payloads, and `structured-output` when it accepts multiple `kind` values.

## Input type decision table

| User input | Use |
| --- | --- |
| OpenAPI document | `--input-file-type openapi` |
| AsyncAPI document | `--input-file-type asyncapi` |
| JSON Schema document | `--input-file-type jsonschema` |
| GraphQL SDL | `--input-file-type graphql` |
| Raw JSON sample data | `--input-file-type json` |
| Raw YAML sample data | `--input-file-type yaml` |
| CSV sample data | `--input-file-type csv` |
| Apache Avro schema | `--input-file-type avro` |
| XML Schema | `--input-file-type xmlschema` |
| Protocol Buffers or gRPC schema | `--input-file-type protobuf` |
| MCP tool schema | `--input-file-type mcp-tools` |
| Existing Python model, dataclass, TypedDict, or dict schema object | `--input-model path/or/module.py:ObjectName` |

When a JSON or YAML file could be either a schema or data, inspect the content. `$schema`, `openapi`, `asyncapi`, `type`, `properties`, `components`, `$defs`, or `definitions` usually mean schema. Plain example values usually mean raw sample data.

For a Python dictionary containing JSON Schema or OpenAPI content, use `--input-model ./schemas.py:OBJECT_NAME` and add `--input-file-type jsonschema` or `--input-file-type openapi` when needed. Do not choose the `dict` input-file-type value.

## Output type decision table

| Project or user signal | Use |
| --- | --- |
| New project, API validation, general Python model generation | `--output-model-type pydantic_v2.BaseModel` |
| User wants Pydantic validation with dataclass syntax | `--output-model-type pydantic_v2.dataclass` |
| Project uses standard dataclasses and does not need runtime validation | `--output-model-type dataclasses.dataclass` |
| User wants dict-compatible static typing | `--output-model-type typing.TypedDict` |
| User wants high-performance serialization | `--output-model-type msgspec.Struct` |
| Project explicitly requires legacy Pydantic v1 | Verify current CLI support first. Current help does not list a Pydantic v1 output path. |

Before choosing the output type, inspect `pyproject.toml`, `requirements.txt`, `uv.lock`, `poetry.lock`, or installed dependencies when available. Prefer the project convention over the global default.

## Canonical command

```bash
uvx datamodel-codegen \
  --input spec.yaml \
  --input-file-type openapi \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 \
  --preset PRESET
```

Replace `PRESET` with the preset the recommender prints, and add the options it recommends.

For remote URLs or remote refs, use the HTTP extra:

```bash
uvx --from 'datamodel-code-generator[http]' datamodel-codegen \
  --url https://example.com/openapi.yaml \
  --input-file-type openapi \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel
```

For GraphQL, use the GraphQL extra when the one-shot command needs it:

```bash
uvx --from 'datamodel-code-generator[graphql]' datamodel-codegen \
  --input schema.graphql \
  --input-file-type graphql \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel
```

## Target Python version

Infer the minimum Python version from:

1. `pyproject.toml` `requires-python`
2. `.python-version`
3. `runtime.txt`
4. CI config
5. Existing generated model style
6. User instruction

Then pass:

```bash
--target-python-version X.Y
```

Do not accept the CLI default if the project clearly declares a Python version.

## Verification step

Verification is mandatory.

For CI or agent integrations, prefer check mode when an output path exists:

```bash
datamodel-codegen [same options] --output models.py --check
```

If another tool must inspect generated content directly, use structured JSON output:

```bash
datamodel-codegen [same options] --output-format json
```

For single-file output:

```bash
python -c "import pathlib, importlib.util; p=pathlib.Path('models.py'); s=importlib.util.spec_from_file_location('generated_models', p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('import ok')"
```

For package output:

```bash
python -c "import models; print('import ok')"
```

Then run project checks when configured:

```bash
ruff check models.py
mypy models.py
pyright models.py
```

Use only the checks that the project already configures or asks for.

After verification, summarize the output path, input type, output model type, target Python version, generated class count if easy to determine, important class names, and warnings or limitations.

Do not dump the whole generated file into the conversation unless the user asks.

## What not to do

* Do not hand-write model files when a usable input schema or sample exists.
* Do not edit generated code by hand before trying the appropriate generator flags.
* Do not silently regenerate with different flags. Say what changed and why.
* Do not add datamodel-code-generator to a user's project dependencies without approval.
* Do not pin a version in user commands unless the repository maintainers decide to pin it.
* Do not claim support for non-Python output targets such as TypeScript.
* Do not use this CLI to fix an existing Pydantic model unless the task is to regenerate or convert models from a source input.

## References

Read `references/workflows.md` for commands by input type and scenario.

Read `references/cli-options.md` when choosing naming, optionality, formatting, splitting, or reuse flags.

Read `references/troubleshooting.md` when generation fails, imports fail, field names look wrong, optionality is surprising, or large specs are slow.
